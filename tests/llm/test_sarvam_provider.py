"""Sarvam chat-completion provider (shotvi/llm/sarvam.py), offline: the HTTP session is a mock, the project spend
ledger is a temp file, no network call is ever made. Covers the request shape (headers, model, messages,
response_format, reasoning_effort, max_tokens), usage -> ledger cost, think-block / fence stripping, bad JSON ->
the client's RetryableError / LLMError path, finish_reason "length" -> the Gemini MAX_TOKENS wording, HTTP errors,
the token-count estimate and the unsupported cache / media paths."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shotvi.llm import client as C
from shotvi.llm import sarvam as S
from shotvi.llm import spend
from shotvi.llm.base import CacheUnavailable, LLMError, ModelSpec, RetryableError

SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "naming_check", "type": "object",
          "required": ["people"], "properties": {"people": {"type": "array", "items": {"type": "string"}}}}
SPEC = ModelSpec("sarvam", "sarvam-105b", {"thinking_level": "medium", "max_output_tokens": 8000})


def http(status=200, body=None):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = body or {}
    r.text = json.dumps(body or {"error": {"message": "x"}})
    return r


def completion(content, prompt=1000, completion_tokens=300, reasoning=200, cached=None, finish="stop"):
    u = {"prompt_tokens": prompt, "completion_tokens": completion_tokens, "total_tokens": prompt + completion_tokens,
         "completion_tokens_details": {"reasoning_tokens": reasoning},
         "prompt_tokens_details": None if cached is None else {"cached_tokens": cached}}
    return {"id": "c1", "object": "chat.completion", "model": "sarvam-105b", "usage": u,
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content, "reasoning_content": "thinking..."}}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(spend, "LEDGER", Path(self.tmp.name) / "llm_spend.json"),
                        mock.patch.dict(os.environ, {"SHOTVI_SPEND_CAP_INR": "100", "SHOTVI_USD_INR": "96"}),
                        # safety net: any real HTTP attempt fails the test
                        mock.patch("requests.Session.post", side_effect=AssertionError("network call"))]
        for p in self.patches:
            p.start()
        self.prov = S.SarvamProvider(api_key="test-key")
        self.prov.session = mock.Mock()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def reply(self, *bodies):
        self.prov.session.post.side_effect = [b if isinstance(b, mock.Mock) else http(200, b) for b in bodies]

    def client(self, cache_dir=None, **kw):
        cl = C.LLMClient(C.Ledger(), cache_dir=cache_dir, **kw)
        return cl


class RequestShape(Base):
    def sent(self, spec=SPEC, schema=SCHEMA, system="SYS"):
        self.reply(completion('{"people": []}'))
        self.prov.generate(spec, system, "PROMPT", schema)
        (url,), kw = self.prov.session.post.call_args
        return url, kw["headers"], kw["json"]

    def test_endpoint_headers_model_messages_schema(self):
        url, headers, body = self.sent()
        self.assertEqual(url, "https://api.sarvam.ai/v1/chat/completions")
        self.assertEqual(headers, {"api-subscription-key": "test-key", "Authorization": "Bearer test-key"})
        self.assertEqual(body["model"], "sarvam-105b")
        self.assertEqual(body["messages"], [{"role": "system", "content": "SYS"},
                                            {"role": "user", "content": "PROMPT"}])
        rf = body["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertEqual(rf["json_schema"]["name"], "naming_check")
        self.assertNotIn("$schema", rf["json_schema"]["schema"])
        self.assertEqual(rf["json_schema"]["schema"]["properties"], SCHEMA["properties"])
        self.assertFalse(rf["json_schema"]["strict"])
        self.assertEqual(body["max_tokens"], 8000)
        self.assertEqual(body["reasoning_effort"], "medium")
        self.assertNotIn("temperature", body)

    def test_no_system_message_when_empty_and_default_max_tokens(self):
        _, _, body = self.sent(ModelSpec("sarvam", "sarvam-105b"), system="")
        self.assertEqual([m["role"] for m in body["messages"]], ["user"])
        self.assertEqual(body["max_tokens"], S.DEFAULT_MAX_TOKENS)
        self.assertNotIn("reasoning_effort", body)   # unset = Sarvam's default

    def test_reasoning_mapping(self):
        _, _, body = self.sent(ModelSpec("sarvam", "sarvam-105b", {"thinking_level": "minimal"}))
        self.assertIn("reasoning_effort", body)
        self.assertIsNone(body["reasoning_effort"])                 # JSON null = reasoning off
        _, _, body = self.sent(ModelSpec("sarvam", "sarvam-105b", {"thinking_level": "low",
                                                                   "reasoning_effort": "high"}))
        self.assertEqual(body["reasoning_effort"], "high")          # Sarvam's own value wins
        with self.assertRaises(LLMError):
            S.SarvamProvider.build_body(ModelSpec("sarvam", "sarvam-105b", {"thinking_level": "huge"}), "", "p", None)

    def test_json_object_and_text_modes_put_schema_in_prompt(self):
        _, _, body = self.sent(ModelSpec("sarvam", "sarvam-105b", {"json_mode": "object"}))
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIn('"required": ["people"]', body["messages"][-1]["content"])
        _, _, body = self.sent(ModelSpec("sarvam", "sarvam-105b", {"json_mode": "text"}))
        self.assertNotIn("response_format", body)
        self.assertIn("JSON Schema", body["messages"][-1]["content"])
        _, _, body = self.sent(ModelSpec("sarvam", "sarvam-105b", {"schema_in_text": True}))
        self.assertNotIn("response_format", body)
        self.assertEqual(body["messages"][-1]["content"], "PROMPT")
        _, _, body = self.sent(schema=None)
        self.assertNotIn("response_format", body)

    def test_temperature_passed_when_set(self):
        _, _, body = self.sent(ModelSpec("sarvam", "sarvam-105b", {"temperature": 0.3}))
        self.assertEqual(body["temperature"], 0.3)

    def test_unsupported_inputs(self):
        with self.assertRaises(LLMError):
            self.prov.generate(SPEC, "", "p", None, media=[{"file_uri": "x"}])
        with self.assertRaises(LLMError):
            self.prov.generate(SPEC, "", "p", None, cached_content="cachedContents/x")
        with self.assertRaises(LLMError):
            S.SarvamProvider.build_body(ModelSpec("sarvam", "sarvam-105b", {"google_search": True}), "", "p", None)
        with self.assertRaises(CacheUnavailable):
            self.prov.create_cache(SPEC, "sys", "prefix")
        self.assertFalse(self.prov.delete_cache("x"))
        self.prov.session.post.assert_not_called()


class UsageAndCost(Base):
    def test_usage_split_and_ledger_cost(self):
        self.reply(completion('{"people": ["a"]}', prompt=10_000, completion_tokens=3000, reasoning=2000, cached=4000))
        cl = self.client()
        with mock.patch.dict(C.PROVIDERS, {"sarvam": self.prov}):
            obj, raw = cl.generate_json("naming_check", SPEC, "S", "P", SCHEMA, want_raw=True)
        self.assertEqual(obj, {"people": ["a"]})
        self.assertEqual((raw.input_tokens, raw.output_tokens, raw.thought_tokens, raw.cached_tokens),
                         (10_000, 1000, 2000, 4000))
        # Rs 15 / 1M uncached input, Rs 5 / 1M cached, Rs 60 / 1M output (reasoning included), at Rs 96 / USD
        inr = (6000 * 15 + 4000 * 5 + 3000 * 60) / 1e6
        self.assertAlmostEqual(cl.ledger.summary()["usd_billed"], round(inr / 96, 5), places=6)
        self.assertEqual(cl.ledger.summary()["output_tokens"], 3000)
        d = json.loads(spend.LEDGER.read_text())                    # project rupee ledger
        e = d["entries"][-1]
        self.assertEqual((e["model"], e["in"], e["out"], e["cached_in"]), ("sarvam-105b", 10_000, 3000, 4000))
        self.assertAlmostEqual(e["usd"] * 96, inr, places=4)
        self.assertEqual(cl.ledger.summary()["unpriced_models"], [])

    def test_reasoning_not_inside_completion_is_never_undercounted(self):
        raw = S.parse_response(completion("{}", completion_tokens=100, reasoning=500), "sarvam-105b")
        self.assertEqual((raw.output_tokens, raw.thought_tokens), (100, 500))
        raw = S.parse_response({"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}, "sarvam-105b")
        self.assertEqual((raw.input_tokens, raw.output_tokens, raw.thought_tokens), (0, 0, 0))

    def test_spend_cap_refuses_before_the_call(self):
        spend.LEDGER.write_text(json.dumps({"total_usd": 200 / 96, "calls": 1, "entries": []}))
        with self.assertRaises(Exception) as cm:
            self.prov.generate(SPEC, "", "p", None)
        self.assertEqual(type(cm.exception).__name__, "BudgetExceeded")
        self.prov.session.post.assert_not_called()

    def test_count_tokens_is_a_local_estimate(self):
        from shotvi.segment.render import estimate_tokens
        t = "ఇది ఒక test sentence"
        self.assertEqual(self.prov.count_tokens(SPEC, t), estimate_tokens(t))
        self.prov.session.post.assert_not_called()


class TextCleaning(Base):
    def test_think_block_and_fence_stripped(self):
        self.reply(completion('<think>maybe {"people": ["x"]} or not</think>\n```json\n{"people": ["b"]}\n```'))
        with mock.patch.dict(C.PROVIDERS, {"sarvam": self.prov}):
            obj = self.client().generate_json("naming_check", SPEC, "S", "P", SCHEMA)
        self.assertEqual(obj, {"people": ["b"]})

    def test_unopened_think_block(self):
        self.assertEqual(S.clean_text('reasoning {"a": 1} here</think>\n{"a": 2}'), '{"a": 2}')
        self.assertEqual(S.clean_text('{"a": 3}'), '{"a": 3}')
        self.assertEqual(S.clean_text(None), "")


class Failures(Base):
    def test_bad_json_is_retryable_then_llmerror(self):
        self.reply(completion("not json at all"), completion('{"people": "wrong type"}'), completion("{oops"))
        cl = self.client(schema_retries=2)
        with self.assertRaises(RetryableError):
            cl._parse(S.clean_text("not json"), SCHEMA)
        with mock.patch.dict(C.PROVIDERS, {"sarvam": self.prov}), self.assertRaises(LLMError) as cm:
            cl.generate_json("naming_check", SPEC, "S", "P", SCHEMA)
        self.assertIn("no valid JSON after 3 attempts", str(cm.exception))
        self.assertEqual(self.prov.session.post.call_count, 3)
        self.assertEqual(cl.ledger.summary()["n_llm_calls"], 3)   # every bad answer was still billed

    def test_bad_answer_never_cached_good_answer_is(self):
        with tempfile.TemporaryDirectory() as cdir:
            self.reply(completion("nope"), completion('{"people": []}'))
            cl = self.client(cache_dir=cdir, schema_retries=1)
            with mock.patch.dict(C.PROVIDERS, {"sarvam": self.prov}):
                self.assertEqual(cl.generate_json("naming_check", SPEC, "S", "P", SCHEMA), {"people": []})
                self.prov.session.post.reset_mock()
                self.reply(completion('{"people": ["fresh"]}'))
                # attempt-0 key holds no answer (the bad one was not cached): a rerun calls again
                self.assertEqual(cl.generate_json("naming_check", SPEC, "S", "P", SCHEMA), {"people": ["fresh"]})
                self.assertEqual(self.prov.session.post.call_count, 1)
                self.assertEqual(cl.generate_json("naming_check", SPEC, "S", "P", SCHEMA), {"people": ["fresh"]})
                self.assertEqual(self.prov.session.post.call_count, 1)   # now a cache hit

    def test_length_is_max_tokens_with_billed_response(self):
        self.reply(completion("", completion_tokens=8000, reasoning=8000, finish="length"))
        with self.assertRaises(RetryableError) as cm:
            self.prov.generate(SPEC, "", "p", SCHEMA)
        self.assertTrue(C.LLMClient.is_max_tokens(cm.exception))
        self.assertEqual(cm.exception.response.thought_tokens, 8000)
        self.assertEqual(json.loads(spend.LEDGER.read_text())["entries"][-1]["out"], 8000)

    def test_empty_content_and_other_finish_reasons_retry(self):
        self.reply(completion("", finish="stop"), completion("{}", finish="content_filter"))
        with self.assertRaises(RetryableError):
            self.prov.generate(SPEC, "", "p", None)
        with self.assertRaises(RetryableError):
            self.prov.generate(SPEC, "", "p", None)

    def test_http_status_mapping(self):
        for status, exc in ((429, RetryableError), (503, RetryableError), (500, RetryableError),
                            (400, LLMError), (403, LLMError), (422, LLMError)):
            self.reply(http(status))
            with self.assertRaises(exc, msg=str(status)):
                self.prov.generate(SPEC, "", "p", None)
        self.assertFalse(spend.LEDGER.exists())   # nothing billed on an HTTP error

    def test_network_error_is_retryable(self):
        import requests
        self.prov.session.post.side_effect = requests.ConnectionError("down")
        with self.assertRaises(RetryableError):
            self.prov.generate(SPEC, "", "p", None)


class Registration(unittest.TestCase):
    def test_load_provider_reads_env_key(self):
        with mock.patch.dict(os.environ, {"SARVAM_API_KEY": "k"}):
            p = C._load_provider("sarvam")
        self.assertIsInstance(p, S.SarvamProvider)
        self.assertEqual(p.timeout, 300.0)

    def test_missing_key_is_llmerror(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("dotenv.load_dotenv"), \
                self.assertRaises(LLMError):
            S.SarvamProvider()


if __name__ == "__main__":
    unittest.main()
