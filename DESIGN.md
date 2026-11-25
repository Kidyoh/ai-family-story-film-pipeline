# Design

## The audience, and what the film owes them

Two audiences, in this order: my mother's generation, who knew her, and my
cousins born in Addis and abroad, who know her name and nothing else. Then the
cohort.

What that changes: the film cannot be a biography, because I do not have the
facts of one. It is one decision, dramatised, and it ends on the living. A
cousin who never met her should finish it knowing one true thing about her and
wanting to ask an elder for the rest.

## Shape

Four scenes, twelve shots, 82 s of picture and 8 s of end cards.

| # | scene | shot | len | mode | why it exists |
|---|-------|------|-----|------|----------------|
| 1 | A | A1 extreme wide, hut at night | 6 s | Wan | where and when, with the celebration off-frame pressing in |
| 2 | A | A2 medium, girl against the wall | 7 s | Ken Burns | she is a child, dressed for someone else's decision |
| 3 | A | A3 close, one tear in lamplight | 6 s | Ken Burns | the moment she understands what is happening |
| 4 | A | A4 insert, an elder's shadow | 6 s | Ken Burns | the institution, as a shadow, without a face |
| 5 | B | B1 wide, fog on the path | 7 s | Wan | the decision as an image, not a line |
| 6 | B | B2 insert, bare feet, blood | 6 s | Ken Burns | what running actually costs; no romanticising |
| 7 | B | B3 close, shawl abandoned | 6 s | Ken Burns | the vow, said with an object |
| 8 | B | B4 extreme wide, the valley | 7 s | Wan | distance: the valley behind, the city unseen |
| 9 | C | C1 wide, Addis in gold | 8 s | Ken Burns | the hard cut from fog to gold is the film's turn |
| 10 | C | C2 close, first smile | 8 s | Ken Burns | the one shot where she is allowed to be glad |
| 11 | D | D1 close, coffee ceremony | 7 s | Wan | the match cut: the same face, sixty years on |
| 12 | D | D2 insert, two hands | 8 s | Ken Burns | memory lives in hands; pull back to the granddaughter |

Nothing is longer than eight seconds and nothing repeats a beat. The two shots
that carry the most meaning - the tear and the hands - are the cheapest and
stillest in the film, which is the argument for the hybrid approach: motion
where it adds, stillness everywhere else.

The `mode` column is the intent, not what ran. This build had no video endpoint
(the Cloudflare account exposes 52 AI models and none of them generate video),
so all twelve shots went through the local treatment in `shots.py`: an eased
camera move, a handheld drift, a small light flicker and fixed-seed grain. That
makes a still read as filmed; it cannot make smoke, steam or cloth move, which
is what the four Wan shots were for.

## Look

Hand-painted gouache and ink on coarse paper. This is a likeness safeguard
first: a painting of my grandmother is a depiction, not a resurrection, and it
cannot be mistaken for a photograph of her. It is also the only way to keep one
person recognisable across twelve independently generated frames - the style
block does the work that a consistent camera crew would do.

Colour is carried by scene, not by shot: cold indigo with one amber lamp (A),
slate-blue fog (B), warm gold and terracotta (C), honey lamplight and deep
green (D). Inside a scene the palette string is identical in every prompt, so
the cuts read as one night, one dawn, one afternoon, one room.

The one real photograph is used at low strength as an image-to-image reference,
for face structure and the age in her features. It is never composited, never
shown, never published. The girl is generated from the written description
alone: a photograph of her at seventy cannot tell a model what she looked like
at thirteen, and inventing a young face from the old one would be a different
kind of lie. She shares a seed, not a face - the film cuts from her to her older
self at the same angle and lets the audience do the recognition.

## Sound

**One narrator.** An older woman, unhurried, close to the microphone, speaking
Amharic as if to one person in the room. A second voice for the ending was
considered and rejected: the film is one woman remembering, and two voices would
turn it into a dialogue it is not. The voice is `Kore` in the contract, with the
style instruction beside it, so changing the performance is a one-line edit.

**The narration is first person and invented.** I do not know what she said that
night. The words are the shape of the story as my family tells it, not a record.
`ETHICS.md` lists exactly which details are remembered and which are invented,
and the film's disclosure card does not pretend otherwise.

**Score** is four cues, one per scene, and it carries the arc: C minor with a
distant frame drum (a room she cannot leave), D minor ostinato (fear with
forward motion), F major swell (the exhale in Addis), F major returning thinned
to one plucked note (the last line lands in near-silence). The opening theme
returns at the end because the ending is the opening, paid off.

The mix is a sidechain duck, not a static level: the score drops when she speaks
and comes back in the gaps, with a slow 1.5 s release so it never pumps. Target
-14 LUFS / -1 dBTP, measured twice - once to normalise, once to verify.

## Text on screen

Subtitles are English, from the contract's `subtitle` field: a translation, not
a transcript, and written to be read in the time the line takes, wrapped to two
lines of about forty characters at the lower third, clear of the safe area on a
phone. The narration stays Amharic; nothing is dubbed over it.

Two end cards. The dedication is Amharic first (`ለአያቴ...`) with the English
under it. The second says plainly that images, music and narration were
generated, in English and Amharic, in the same type.

## One master, two exports

The film is composed for 9:16, because it will be watched on a phone, once, by
people who are standing up. The 16:9 export is derived: the vertical frame stays
sharp in the centre and the sides are filled with a blurred, dimmed extension of
the same image. It looks deliberate, it costs one FFmpeg pass, and it does not
pretend the horizontal cut was designed shot by shot. Composing twice would have
doubled image and video spend for a secondary deliverable.

## What the audience test still has to answer

Three people who knew her will watch it before anything is published: my mother,
one of her siblings, one cousin from the Addis generation. The questions are
narrow - does the face read as her, does the voice feel like her, is any invented
detail wrong in a way that hurts. Their notes go into this file and into the
next take; nothing is published until they have seen it.
