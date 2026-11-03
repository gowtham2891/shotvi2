# Hand-pinned reference faces

`<video_id>.yaml` with `{key: ["m:ss.s", ...]}` replaces the automatic reference frames for those keys in Stage 2.
Check the "Reference faces" expander in the app's Stage 2 tab: if a key's frames show the wrong person (a listener
in a wide shot), pin two or three times where that person is clearly alone on screen, e.g.

```yaml
I: ["10:23.2", "9:35.0"]
K: ["15:47.5"]
```

A pin can also carry the look sentence (it replaces the cast sheet's for that key, and is saved to the bank):

```yaml
I: {times: ["10:23.2"], look: "heavy-set young man with a full beard, olive-green shirt"}
```
