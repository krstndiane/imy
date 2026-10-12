# Rebuilding the Inter font files

The site serves its own copy of Inter (`frontend/public/media/fonts/`) instead of loading it from
fonts.googleapis.com. You only need this page if you want to update Inter or add characters to it —
the files are already built and committed, so a normal deploy needs nothing from here.

**What's there:** `inter-latin.woff2` (~66 KB) and `inter-latin-ext.woff2` (~121 KB). Each is one
variable font carrying every weight from 100 to 900, so the whole site needs one download instead of
one per weight. `media/fonts.css` declares them with `unicode-range`, so a browser only fetches
latin-ext if a page actually shows a character from it (an accented name, the peso sign).

**Licence:** Inter by Rasmus Andersson, SIL Open Font License 1.1. The licence sits beside the fonts
in `fonts/LICENSE.txt` and has to stay there — that's the one condition of using it.

## To rebuild

Needs Python with `fonttools` and `brotli` (`pip install fonttools brotli`).

```bash
# 1. the font itself
git clone --depth 1 https://github.com/rsms/inter
cd inter/docs/font-files

# 2. the character ranges, matching Google's own latin / latin-ext subsets, so the site looks
#    exactly as it did when it loaded Inter from Google
LATIN="U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,\
U+2000-206F,U+2074,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"
EXT="U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+1D00-1DBF,U+1E00-1E9F,\
U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF"

# 3. cut the font down to those ranges
for pair in "latin:$LATIN" "latin-ext:$EXT"; do
  pyftsubset InterVariable.woff2 \
    --output-file="inter-${pair%%:*}.woff2" --flavor=woff2 --with-zopfli \
    --unicodes="${pair#*:}" \
    --layout-features="kern,liga,calt,ccmp,locl,mark,mkmk,tnum,case" \
    --no-hinting --desubroutinize
done

# 4. put them in place, keeping the licence beside them
cp inter-latin.woff2 inter-latin-ext.woff2  <imy>/frontend/public/media/fonts/
cp ../../LICENSE.txt                        <imy>/frontend/public/media/fonts/LICENSE.txt
```

Then rebuild the site and check in a browser that the page still asks for nothing outside your own
address (open the Network tab and look for anything that isn't your domain).

**If you add characters:** widen the ranges above, not just the CSS — a character that isn't in the
file will quietly fall back to the system font. Emoji are meant to fall back: they're deliberately
left out so an iPhone draws its own, which is why 😼 looks right in a chat.
