"""smalltalk.py - answer what was actually said, without an AI model.

The screenshots teach HOW the person writes (casing, length, endearments, language);
they are not a script. So replies are written here, by topic, and engine.py then
applies the person's style on top.

How it works: RULES is an ordered list. The first rule whose pattern matches your
message supplies the reply. Each rule has Tagalog/Taglish lines and English lines;
the person's own language decides which are used. "{e}" becomes one of their
endearments (sometimes). To teach it a new topic, add a rule.

This is pattern matching, not understanding: anything no rule covers gets a neutral
reply from personality.py. For real conversation, turn on Claude mode (claude.py).
"""
import re
from datetime import datetime

LAUGH = r"(?:\b(?:ha|he){2,}h?\b|\blo+l\b|\blmao\b|😂|🤣)"

# (name, pattern, tagalog lines, english lines)
RULES = [
    ("time", r"\b(anong oras|what time)\b",
     ["mag {time} na", "{time} na"], ["it's {time}", "{time} already"]),
    ("day", r"\b(anong araw|what day)\b",
     ["{day} ngayon", "{day} na"], ["it's {day}", "{day}"]),
    ("who", r"\b(sino ka|who are you|who is this|sino (to|ito))\b",
     ["si {name} hahaha, sino pa ba", "{name} to hahaha"], ["it's {name} haha, who else", "{name}, obviously"]),
    ("age", r"\b(ilang taon ka|how old are you)\b",
     ["{age} hahaha bakit", "{age} na"], ["{age} haha, why", "{age}"]),

    ("cant", r"\b((di|hindi|hinde) (ako |ko |na ako )?(pwede|puwede|makaka\w*|kaya)|can'?t (make it|go|come)|i'?m busy|busy ako)\b",
     ["ay sayang, sige next time", "oks lang{e}, kelan ka free?", "sige sige, sabihan mo ko"],
     ["aw too bad, next time then", "that's okay{e}, when are you free?", "sure, just let me know"]),
    ("favorite", r"\b(fave|fav|favou?rite|paborito)\b",
     ["hmm wala akong specific eh, ikaw?", "dami eh hahaha, ikaw ano sayo?"],
     ["hmm i don't really have one, you?", "too many haha, what's yours?"]),
    ("story", r"\b(pwede|puwede|pede|can i|could i)\b.*\b(m?a?g ?kwento|kwento|share|sabihin|tanong|magtanong|rant|vent|tell you|ask)\b"
              r"|\bmay (kwento|chika|sasabihin|tanong|ikukwento) ako\b|\bguess what\b|\bi have (something|news)\b",
     ["sige kwento ka, nakikinig ako", "oo naman{e}, ano yun?", "sige go, ano yun"],
     ["sure, go ahead{e}", "of course, what is it?", "yeah, tell me"]),

    ("likes_me", r"\b(gusto|mahal|crush|miss|namimiss|love) mo (ba |na )?(ako|ko)\b|\bdo you (like|love|miss) me\b|\bmiss me\b",
     ["oo naman{e}", "syempre naman", "oo hahaha bakit"], ["of course{e}", "obviously haha", "yeah, why"]),
    ("miss_you", r"\b(i miss you|miss you|miss u|miss (na )?kita|na-?miss kita|namimiss kita|i missed you)\b",
     ["miss you too{e}", "miss na rin kita{e}"], ["miss you too{e}", "i miss you too{e}"]),
    ("love_you", r"\b(i love you|love you|luv u|love u|ily|mahal kita)\b",
     ["love you too{e}"], ["love you too{e}"]),
    ("thanks", r"\b(thanks|thank you|thx|ty|salamat)\b",
     ["wala yun{e}", "no prob{e}", "anytime{e}"], ["of course{e}", "anytime{e}", "no problem"]),
    ("sorry", r"\b(sorry|sori|patawad|pasensya)\b",
     ["oks lang yun{e}", "wala yun, wag mo na isipin"], ["it's okay{e}", "don't worry about it"]),
    ("goodnight", r"\b(good ?night|gn|sleep well|sweet dreams|tulog na ako|matutulog na ako)\b",
     ["goodnight{e}", "sige tulog ka na{e}", "goodnight, pahinga ka"], ["goodnight{e}", "sleep well{e}"]),
    ("goodmorning", r"\b(good ?morning|magandang umaga|gm)\b",
     ["good morning{e}", "morning{e}, kamusta tulog mo?"], ["good morning{e}", "morning{e}, sleep well?"]),
    ("bye", r"\b(bye+|babye|alis na ako|una na ako|see you|brb|ttyl|talk later|mamaya na lang)\b",
     ["sige ingat{e}", "sige, chat ka pag free ka", "ingat ka"], ["okay, take care{e}", "sure, talk later"]),

    ("you_ok", r"\b(ok(ay)? ka (lang|ba|pa)|ayos ka (lang|ba)|are (you|u) (ok(ay)?|alright|good)|(you|u) ok(ay)?|you good)\b",
     ["oo okay lang ako{e}, ikaw?", "oo naman, bakit?", "okay lang, ikaw okay ka lang?"],
     ["yeah i'm okay{e}, you?", "i'm fine, why?", "all good, are you okay?"]),
    ("busy_q", r"\b(galit ka( ba)?|are you mad|mad at me|tampo ka( ba)?|nagtatampo ka)\b",
     ["hindi ah, bakit naman", "di naman{e}, bakit mo naisip yan?"], ["no, why would i be", "not at all{e}, why do you think that?"]),
    ("remember", r"\b(naaalala mo|natatandaan mo|tanda mo( pa)?|do you remember|remember (when|that|me))\b",
     ["oo naman, bakit?", "medyo hahaha, ano ulit yun?"], ["of course, why?", "kind of haha, remind me?"]),
    ("how_are_you", r"\b(how are you|how r u|how('?s| is) (it going|everything|your day)|hbu|wbu|(k[au])?musta|what'?s up|sup)\b",
     ["okay lang naman{e}, ikaw?", "oks lang{e}, ikaw kamusta", "eto buhay pa hahaha, ikaw?"],
     ["i'm good{e}, how about you?", "doing okay{e}. you?"]),
    ("doing", r"\b(what are you doing|what r u doing|wyd|whatcha doing|ano(ng)? (gina)?gawa mo|gawa mo)\b",
     ["wala naman{e}, ikaw?", "eto nakahiga lang hahaha, ikaw?", "nagpapahinga lang{e}"],
     ["nothing much{e}, you?", "just resting{e}. you?"]),
    ("eaten", r"\b(have you eaten|did you eat|kumain ka na|kain ka na|nag ?(lunch|dinner|breakfast) ka na)\b",
     ["oo kanina pa, ikaw?", "di pa eh, ikaw?", "tapos na{e}, ikaw kumain ka na?"],
     ["yeah, a while ago. you?", "not yet{e}, you?"]),
    ("where", r"\b(where are you|where r u|wru|(na)?sa?an ka( na)?|asan ka( na)?)\b",
     ["bahay lang{e}", "dito lang sa bahay, bakit?"], ["just at home{e}", "home, why?"]),
    ("awake", r"\b(are you (still )?(awake|up)|still up|gising ka pa|tulog ka na ba)\b",
     ["oo gising pa{e}, ikaw?", "gising pa, bakit?"], ["yeah still up{e}, you?", "still up, why?"]),
    ("free", r"\b(busy ka( ba)?|free ka( ba)?|are you (busy|free)|may ginagawa ka( ba)?)\b",
     ["free naman ako, bakit?", "oo free ako, ano meron?"], ["i'm free, why?", "yeah i'm free, what's up?"]),
    ("why_didnt", r"\b(bakit|bkt) (di|hindi|hinde) (ka|mo)\b|\bwhy (didn'?t|did not|don'?t|haven'?t) (you|u)\b",
     ["sorry{e}, naging busy lang", "di ko napansin eh, sorry", "sorry na, babawi ako"],
     ["sorry{e}, i got busy", "i didn't notice, sorry", "my bad, i'll make it up to you"]),
    ("offer", r"\bgusto mo (ba )?(ng|mag|ng? ?kumain|sumama)\b|\b(do you want|want (some|to)|wanna)\b",
     ["sige, g", "oo naman, tara", "pwede rin hahaha"], ["sure, i'm down", "yeah, let's", "why not haha"]),

    ("advice", r"\b(dapat ko(ng)? gawin|ano(ng)? gagawin ko|what should i do|what do i do|payo|advice)\b",
     ["hmm kausapin mo muna nang maayos", "pag isipan mo muna bago ka mag decide", "ikaw, ano ba gusto mo mangyari?"],
     ["hmm, talk it through calmly first", "think it over before you decide", "what do you want to happen?"]),
    ("opinion", r"\b(ano(ng)? sa tingin mo|sa tingin mo|what do you think|okay lang ba|oks lang ba|ayos lang ba|is (it|that) ok(ay)?|dapat ba)\b",
     ["oks lang naman para sakin", "ikaw bahala, kung san ka masaya", "pwede naman, ikaw ano gusto mo?"],
     ["sounds okay to me", "up to you, whatever makes you happy", "could work, what do you want?"]),
    ("invite_when", rf"(?=.*\b({'bukas|mamaya|ngayon|tonight|tomorrow|later|weekend|sabado|linggo|lunes|martes|miyerkules|huwebes|biyernes|saturday|sunday|monday|tuesday|wednesday|thursday|friday'})\b)"
                    r".*\b(tara|gala tayo|labas tayo|kita tayo|magkita tayo|punta tayo|sama ka|kain tayo|nood tayo|hang ?out|let'?s (go|meet|eat|watch)|wanna (go|meet|eat|watch))\b",
     ["g ako{e}", "sige g, anong oras?", "tignan ko sched ko ha"], ["i'm down{e}", "sure, what time?", "let me check my schedule"]),
    ("invite", r"\b(tara|gala tayo|labas tayo|kita tayo|magkita tayo|punta tayo|sama ka|kain tayo|nood tayo|hang ?out|let'?s (go|meet|eat|watch)|wanna (go|meet|eat|watch))\b",
     ["g ako, kelan?", "sige, kelan ba?", "tignan ko sched ko ha"], ["i'm down, when?", "sure, when?", "let me check my schedule"]),

    ("conflict", r"\b(nag-? ?away|away kami|inaway|nag-? ?break|iniwan|niloko|nasaktan|sama ng loob|may problema|had a fight|we fought|broke up|argument)\b",
     ["hala bakit, ano nangyari?", "uy okay ka lang ba?", "kwento mo, nakikinig ako"],
     ["oh no, what happened?", "hey, are you okay?", "tell me, i'm listening"]),
    ("annoyed", r"\b(nakakainis|kainis|naiinis|inis|bwisit|nakakabwisit|galit ako|annoying|annoyed|pissed)\b",
     ["bakit, ano ginawa?", "hala ano nangyari", "gets kita, nakakainis nga yan"],
     ["why, what did they do?", "oh no what happened", "i get it, that's annoying"]),
    ("sick", r"\b(may sakit|masakit|nilalagnat|lagnat|sipon|ubo|sick|fever|headache|sakit ng (ulo|tiyan))\b",
     ["uy pahinga ka, inom ka gamot", "hala, inom ka water tas pahinga{e}", "pagaling ka{e}"],
     ["oh no, get some rest and take meds", "drink water and rest{e}", "get well soon{e}"]),
    ("down", r"\b(sad|malungkot|lungkot|umiiyak|naiiyak|iyak|stress(ed)?|na-?stress|anxious|kabado|takot|lonely|upset|bad day|nahihirapan|hirap na|overwhelmed|pagod|tired|exhausted|drained)\b",
     ["bakit, ano nangyari?", "pahinga ka muna{e}", "andito lang ako", "kaya mo yan{e}"],
     ["what happened?", "get some rest{e}", "i'm here", "you've got this{e}"]),
    ("bored", r"\b(bored|boring|wala(ng)? magawa|nababagot|naiinip)\b",
     ["same hahaha, ano trip mo gawin?", "nood ka muna or tulog hahaha"], ["same haha, what do you feel like doing?", "watch something or nap haha"]),
    ("sleepy", r"\b(antok|inaantok|sleepy|puyat|napuyat|di (ako )?makatulog|can'?t sleep)\b",
     ["tulog ka na{e}", "wag ka na magpuyat", "sige pahinga ka na"], ["go to sleep{e}", "don't stay up too late", "get some rest"]),
    ("hungry", r"\b(gutom|nagugutom|hungry|starving)\b",
     ["kain ka na{e}", "hala kain ka na, ano gusto mo?"], ["go eat{e}", "eat something, what do you want?"]),
    ("food", r"\b(kumain ako|kakain|kain|ulam|food|lunch|dinner|breakfast|merienda|kape|coffee|milk ?tea|jollibee|mcdo)\b",
     ["uy sarap, ano kinain mo?", "nice, busog ka na?", "sana all hahaha"], ["nice, what did you have?", "sounds good, are you full?"]),
    ("school_work", r"\b(exam|quiz|nag-?aral|mag-?aral|aral|review|thesis|assignment|deadline|school|klase|prof|work|trabaho|shift|overtime|meeting|report|project)\b",
     ["kaya mo yan{e}", "good luck dyan, kaya yan", "pahinga rin minsan ha"], ["you've got this{e}", "good luck with that", "take breaks too, okay"]),
    ("good_news", r"\b(pumasa|nakapasa|passed|nanalo|won|promoted|natanggap|accepted|finally|sa wakas|good news|excited|ang saya)\b",
     ["uy nice, congrats{e}", "ayos yan, proud ako sayo", "galing naman"], ["nice, congrats{e}", "that's great, proud of you", "let's gooo"]),
    ("weather", r"\b(umuulan|ulan|mainit|init|malamig|lamig|bagyo|baha|raining|so hot|so cold)\b",
     ["oo nga eh, ingat ka", "grabe nga eh", "wag ka magpaulan ha"], ["yeah, stay safe", "i know right", "don't get caught in it"]),

    ("compliment", r"\b(ganda|pogi|gwapo|cute|galing|bait|sweet|proud ako sa ?yo|ang (cool|talino))\b",
     ["hala hahaha thank you", "ikaw rin naman", "wag ganyan hahaha"], ["aw haha thank you", "you too", "stop it haha"]),
    ("tease", r"\b(baliw|loko|sira ka|pangit|panget|ang (corny|daya|arte))\b",
     ["hala bakit hahaha", "grabe ka naman", "ano ginawa ko hahaha"], ["hey why haha", "wow okay", "what did i do haha"]),

    ("why", r"^(bakit|bkt|why)( naman| ba| though)?\W*$",
     ["wala lang hahaha", "basta", "trip ko lang hahaha"], ["no reason haha", "just because", "i felt like it haha"]),
    ("yes", r"^(oo+|yes+|yup|opo|sige+|g|ok(ay)?|oki+|oks|noted|gets|true|totoo)( nga| naman| lang| po)*\W*$",
     ["sige sige", "oks", "ayos"], ["okay", "alright", "cool"]),
    ("no", r"^(hindi|hinde|di|no+|nope|nah|ayoko|ayaw|wag)( naman| na| po)*\W*$",
     ["ay bakit naman", "sige oks lang", "hala bakit"], ["oh, why", "okay, that's fine", "aw why"]),
    ("fine", r"^(ok(ay)?|oks|ayos|good|fine|mabuti|goods)( lang| naman| rin| din| ako| too)*\W*$",
     ["buti naman", "ayos yan", "nice nice"], ["good to hear", "that's good", "nice"]),
    ("greeting", r"^\W*(hi+|hello+|hey+|yo|hoy+|uy+|oy+)\b(\s+\w+){0,2}\W*$",
     ["uy{e}", "hi{e}", "hello{e}, kamusta?"], ["hey{e}", "hi{e}", "hello{e}, how are you?"]),
    ("laugh", rf"^(?=.*{LAUGH})(\W*\w+){{1,5}}\W*$",
     ["hahaha", "hahaha totoo", "loko hahaha"], ["hahaha", "lol", "haha stop"]),
]
# These rules react to something you tell them; they make no sense as an answer to a question.
STATEMENT_ONLY = {"food", "school_work", "weather", "sleepy", "hungry", "good_news", "compliment", "bored"}
COMPILED = [(name, re.compile(rx, re.I), tl, en) for name, rx, tl, en in RULES]
# After the user answers something we asked, acknowledge it instead of firing another question.
ACK_TL = ["ah oks oks", "ahh gets", "nice naman", "buti naman"]
ACK_EN = ["ah okay", "oh i see", "nice", "that's good"]


def covers(user_text: str) -> bool:
    """Does any rule recognise this message?"""
    return any(rx.search(user_text.strip()) for _, rx, _, _ in COMPILED)


def reply(user_text: str, history: list[dict], st: dict, answers: dict, name: str, rng, recent=()) -> tuple[str, str] | None:
    """Returns (text with {e} still in it, why) or None when no rule covers the message."""
    t = user_text.strip()
    asking = "?" in t or bool(re.search(r"\bba\b", t, re.I))
    tagalog = bool(st.get("tagalog"))
    last = next((h["text"] for h in reversed(history) if h["role"] == "person"), "")
    now = datetime.now()
    fill = {
        "time": now.strftime("%I:%M").lstrip("0"), "day": now.strftime("%A"),
        "name": (answers.get("q2") or name).strip(), "age": (answers.get("q4") or "").strip(),
    }
    for rule, rx, tl, en in COMPILED:
        if not rx.search(t) or (rule == "age" and not fill["age"]):
            continue
        if rule in STATEMENT_ONLY and asking:   # "kumain ako" is news; "gusto mo ng kape?" is a question
            continue
        lines = tl if tagalog else en
        if last.rstrip().endswith("?"):   # we just asked something: don't answer with another question
            lines = [l for l in lines if not l.rstrip().endswith("?")] or lines
        fresh = [l for l in lines if l.format(e="", **fill).lower() not in recent] or lines
        return rng.choice(fresh).format(e="{e}", **fill), f"your message reads as “{rule.replace('_', ' ')}”"
    # No rule, but they are answering our question with a plain statement.
    if last.rstrip().endswith("?") and "?" not in t and len(t.split()) <= 12:
        pool = ACK_TL if tagalog else ACK_EN
        fresh = [l for l in pool if l not in recent] or pool
        return rng.choice(fresh), "you answered their question, so they acknowledge it"
    return None
