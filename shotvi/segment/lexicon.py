"""Small, editable word lists used by the segmenter.

These are *hints*, never the sole rule: backchannels are detected primarily by
shape (short, few words, surrounded by / overlapping the other speaker), and
questions primarily by punctuation and turn position. Keep entries normalized
(see ``text.norm``: lowercase, punctuation stripped). Add freely; nothing here
is tuned to a specific video.
"""

# Listener acknowledgements / reactions. Multi-word entries are matched as a
# whole run ("makes sense"), single tokens are matched per token (so
# "అవును అవును" and "right right" also match).
BACKCHANNEL_TOKENS = {
    # Telugu
    "ఉమ్", "మ్", "ఉం", "ఊ", "ఊం", "ఉహు", "ఊహూ", "ఉహూ", "హూ", "ఊ హూ", "హ్మ్", "మ్-హ్మ్", "మ్హ్మ్",
    "హా", "హాఁ", "ఆ", "ఆఁ", "ఆహా", "ఓ", "ఓహ్", "ఓహో", "అవును", "అవునా", "అవునవును", "అవునండి",
    "అవునండీ", "అవును సార్", "అయ్యో", "అయ్యా", "అబ్బా", "అరె", "అరే", "సరే", "సరేనండి", "కదా",
    "నిజమే", "నిజం", "కరెక్ట్", "కరెక్టే", "ఓకే", "ఓకే ఓకే", "యా", "యా యా", "యెస్", "రైట్", "సూపర్",
    "బాగుంది", "అంతే", "అదే", "హహ", "హహహ", "హాహా", "హీహీ",
    # English (as Scribe writes it in Tenglish transcripts)
    "ok", "okay", "right", "yeah", "yes", "yep", "ya", "yah", "hmm", "mm", "mhm", "uh", "um", "huh",
    "oh", "wow", "nice", "sure", "true", "exactly", "absolutely", "correct", "definitely", "super",
    "great", "cool", "got", "it", "sir", "madam", "anna", "haha", "hahaha",
}

# Multi-word phrases that are backchannels as a whole (tokens alone may not be).
BACKCHANNEL_PHRASES = {
    "makes sense", "very true", "got it", "i see", "that's true", "thats true", "oh okay",
    "oh ok", "very nice", "of course", "true true", "means a lot", "very deep", "right sir",
    "yes sir", "ok sir", "okay sir", "correct sir", "super sir", "అవును sir", "ఓ okay",
}

# Tokens that on their own do not make a run a backchannel (e.g. "it", "got",
# "sir" only count inside a phrase or next to a real backchannel token).
BACKCHANNEL_WEAK = {"it", "got", "sir", "madam", "anna", "కదా", "అంతే", "అదే", "సరే"}

# Speaker fillers: a clip should not start on these.
FILLERS = {"ఉమ్", "మ్", "ఉం", "ఊ", "ఆ", "ఆఁ", "అ", "ఏ", "హ్మ్", "um", "uh", "hmm", "mm", "er", "ah"}

# Words that, when they END a stretch, suggest the sentence continues.
CONTINUATION_END = {
    "and", "but", "or", "so", "because", "the", "a", "an", "of", "to", "in", "on", "at", "for",
    "with", "that", "which", "if", "like", "is", "are", "was", "my", "your", "our", "their",
    "plus", "whereas", "unless", "very", "more", "than", "as",
    "కానీ", "ఎందుకంటే", "అంటే", "మరి", "ఇంకా", "కాబట్టి", "అయితే", "మరియు", "లేదా", "ఒక", "ఈ",
    "ఆ", "నా", "మా", "మీ", "మన", "సో",
}

# Discourse markers that tend to OPEN a new thought.
OPENERS = {
    "so", "సో", "now", "okay", "ok", "actually", "basically", "first", "firstly", "next", "then",
    "anyway", "see", "look", "ఇప్పుడు", "అసలు", "సరే", "ఇంక", "ఇక", "ఫస్ట్", "మొదట", "మొదటిది",
    "తర్వాత", "ఆ తర్వాత", "ఓకే", "చూడండి", "చూడు",
}

# Telugu interrogative stems (matched as token prefixes). Indefinite / clause
# forms built on the same stems are excluded via QUESTION_STEM_EXCLUDE_SUFFIXES.
TELUGU_WH_STEMS = (
    "ఏంటి", "ఏంటీ", "ఏమిటి", "ఏమి", "ఏం", "ఏది", "ఏవి", "ఎందుకు", "ఎలా", "ఎలాగ", "ఎట్లా",
    "ఎట్ల", "ఎప్పుడు", "ఎవరు", "ఎవరి", "ఎవరికి", "ఎవడు", "ఎక్కడ", "ఎన్ని", "ఎంత", "ఎలాంటి",
    "ఏంది", "ఏమైంది", "ఎవ్వరు",
)
# "ఏంటంటే" (the thing is), "ఎందుకంటే" (because), "ఎవరైనా" (anyone), "ఏదో"
# (something), "ఎంతో" (a lot) share the stems but are not questions.
QUESTION_STEM_EXCLUDE_SUFFIXES = ("ంటే", "ైనా", "ైతే", "ో", "ోను", "ా కూడా", "ుంటే", "ాక", "ైన")
# Short stems that must match the whole token (too many false prefixes otherwise).
TELUGU_WH_EXACT_ONLY = {"ఏం", "ఏమి", "ఎంత", "ఎన్ని", "ఎట్ల"}

ENGLISH_WH = {"how", "what", "why", "when", "where", "who", "whom", "whose", "which"}
ENGLISH_WH_CONTRACTED = {"what's", "whats", "how's", "hows", "who's", "where's", "why's", "whatre"}
ENGLISH_OPENERS = {"and", "but", "so", "then", "sir", "ok", "okay", "now", "actually", "anna"}
ENGLISH_SUBJECTS = {"you", "u", "we", "i", "it", "they", "he", "she", "this", "that", "there", "your"}
# after a wh-word: "how you managed", "what about ..."
ENGLISH_PRONOUN_AFTER_WH = {"you", "about", "if", "exactly", "kind", "else", "happened", "made", "makes"}
ENGLISH_AUX_START = {
    "do", "does", "did", "can", "could", "would", "will", "shall", "should", "is", "are", "was",
    "were", "have", "has", "had", "may", "might",
}

# Tokens ending in the Telugu yes/no marker "ా" that are NOT questions.
YESNO_FALSE_FRIENDS = {
    "చాలా", "గా", "లా", "అలా", "ఇలా", "ఎలా", "కదా", "అయినా", "ఐనా", "కూడా", "సినిమా", "మా",
    "నా", "రా", "పదా", "మామా", "అమ్మా", "అన్నా", "అక్కా", "బాబా", "తాతా", "రామా", "దా", "పా",
    "సరిగ్గా", "బాగా", "మెల్లగా", "ఎక్కువగా", "తక్కువగా", "కొత్తగా", "మళ్ళీగా", "ఇప్పటిదాకా",
    "దాకా", "వరకా", "తోటా", "ఏమైనా", "ఎవరైనా", "ఎప్పుడైనా", "ఎక్కడైనా", "ఏదైనా", "అంటున్నా",
    "చేస్తున్నా", "వెళ్తున్నా", "అనుకున్నా", "చూశా", "చెప్పా", "అయ్యా", "అన్నమాట",
}
# Final syllables that carry the yes/no interrogative "-ా".
YESNO_ENDINGS = ("దా", "సా", "వా", "యా", "రా", "నా", "ళ్ళా")
YESNO_STRONG = ("దా", "సా", "వా", "యా")  # also accepted one token before the end
# Concessive verb endings ("even if / -ever"): a wh-word before them is indefinite.
CONCESSIVE_ENDINGS = ("ినా", "ఉన్నా", "అయినా", "ైనా")

# Honorific tails that may follow a question word ("... ఉందా sir?").
TAIL_TOKENS = {"sir", "సార్", "అండి", "అండీ", "గారు", "madam", "మేడమ్", "అన్నా", "బ్రో", "bro", "రా", "అసలు"}

# Audio-event text patterns (Scribe tags events in the transcript language).
LAUGHTER_PATTERNS = ("నవ్వు", "laugh", "chuckle", "giggle")
CENSORED_PATTERNS = ("సెన్సార్", "censor", "bleep", "beep")
MUSIC_PATTERNS = ("పాట", "సంగీతం", "music", "song", "singing")
APPLAUSE_PATTERNS = ("చప్పట్లు", "చప్", "applause", "clap")
SILENCE_PATTERNS = ("నిశబ్దం", "నిశ్శబ్దం", "silence")

# Second-person address: a host turn that talks *to* the guest is a prompt, not a passing comment.
SECOND_PERSON = {
    "మీరు", "మీ", "మీకు", "మిమ్మల్ని", "మీది", "మీరే", "మీతో", "మీలో", "మీదే", "నువ్వు", "నీ", "నీకు", "నిన్ను",
    "నువ్వే", "you", "your", "yours", "yourself", "u",
}
