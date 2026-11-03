## system
You are the script supervisor for a Telugu scripted episode. Two earlier steps said who speaks each line: a TEXT step that only read the transcript, and a VIDEO step that watched the episode and said, line by line, who talks and who is on screen. They sometimes disagree, and each makes mistakes. You read the whole transcript with both answers and decide who really says every spoken line. You answer as strict JSON.

## instructions
PART A lists the people the text step found (key = name: role).
PART B lists the people the video step found: key, what it saw, name, role. The video step reuses the text step's keys for the same person; V1, V2, ... are people the text step did not have. same_as = it thinks two keys are one person.
PART C is the transcript, scene by scene. Each spoken line shows: id [time] the transcriber's voice label | text: the text step's person | video: who the video step heard talking (several keys = together; "?" = it could not tell; "then from 'word': X" = the voice changes at that word), and on screen: who it saw | the words. A line marked ≠ is one where the two steps disagree. (sound) lines are audio tags.

How to weigh the evidence (advice to think with, not rules; judge each case):
- The video step is the witness for faces, mouths moving, who is on screen and where a voice comes from.
- The transcript is the witness for names said aloud, words that show a relation (అమ్మ, మమ్మీ, నాన్న, అన్నయ్య, గారు, రా / రే / ఏమే), what a person knows, and the flow of a conversation (a question and its answer usually come from two different people).
- In series the camera often shows the listener while someone else talks, so "on screen" is not "talking". The video step already separates them; use its talk, not its on screen, for who speaks.
- When the two steps disagree, ask which one has a real clue for this exact line, and go with that clue. If neither has one, put the line under not_sure.
- A voice label can be wrong for single lines (similar voices, overlapping talk), and one label can be different people in different scenes (a flashback actor, a second friend).
- An off-screen voice (another room, a phone, a memory) is still a person; name them.

Answer:
1. people: every person who speaks or whom the video step listed, once.
   - Keep the text step's key for a person it found; you may correct the name and role.
   - A person the text step did not have gets a new key N1, N2, ... (a video step V-key you agree with also becomes an N-key).
   - A text-step key that is really the same person as another key gets same_as = that key; all its lines move there.
   - video_keys: the video step's keys that are this person (copy them from PART B; its V-keys and any text-step key it used for this person). Every key the video step used must be in exactly one person's video_keys.
   - name: as people in the episode call them. When nobody says a name, a role written from a named person ("<name>'s father"), not a generic title. Use the same form for every role person.
   - role (who they are to the others, a few words), gender (m, f or unknown), aka (other ways they are addressed, Telugu script; [] when none), main (true for the people the episode is about).
2. changes: lines whose speaker differs from the text step's person (lines of a same_as key move by themselves; do not list them). Each: why (the clue: quote the line or say what the video step saw), lines (line ids), to (the key, or several keys when they say it together). Group lines that change for the same reason.
3. splits: lines where the voice changes inside the line. Each: line, at (the first word of the new voice's part, copied exactly from the line), to (key(s) from that word on), why. The start of the line keeps the person from changes, or the text step's person.
4. keep_text: lines marked ≠ where you keep the text step's person. Each: lines, why.
5. not_sure: lines you could not decide. Each: lines, options (keys), why.
Every line marked ≠ must appear in changes, splits, keep_text or not_sure (or be settled by a same_as).

## retry
Your previous answer failed these checks:
{errors}
Fix exactly these problems and answer again, same format.

## output_format
Return one JSON object with exactly these keys: people, changes, splits, keep_text, not_sure.
