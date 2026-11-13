# Telugu typing service (AI4Bharat IndicXlit)

Suggests Telugu spellings for a word typed in English letters ("enti" -> ఏంటి, ఎంటి, ...) for the caption editor.
Free and open source; runs on our server, so typed words go nowhere else. The API (backend/api.py, POST
/transliterate) asks it at INDICXLIT_URL (default http://localhost:8010) and returns no suggestions if it is down,
so the editor never breaks.

    docker build -t klipvi-indicxlit:1 services/indicxlit
    docker run -d --name klipvi-indicxlit --restart unless-stopped -p 8010:8000 klipvi-indicxlit:1

Measured on a Mac (Nov 2026): 1.86 GB memory, ~0.3% CPU when idle, 0.09-0.22 s per word, starts in ~8 s, image
11.3 GB (PyTorch + TensorFlow, which the AI4Bharat package pulls in). Kept in its own container: its libraries
(fairseq, old pip) clash with the main app's.
