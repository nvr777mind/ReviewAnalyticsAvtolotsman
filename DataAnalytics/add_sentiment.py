import csv, sys, re, math
import pymorphy3 as pymorphy
from pathlib import Path
from collections import Counter

DEFAULT_CSV = "Csv/Reviews/all_reviews.csv"
TEXT_COL = "text"
SENT_COL = "sentiment"
RATING_COL = "rating"

_morph = pymorphy.MorphAnalyzer()

def lemma(tok: str) -> str:
    t = (tok or "").lower().replace("ё", "е")
    if _morph:
        try:
            return _morph.parse(t)[0].normal_form
        except Exception:
            return t
    return t

def load_local_rusentilex() -> dict:
    candidates = [
        Path("lexicons/rusentilex_2017.txt"),
        Path("lexicons/rusentilex_2017.tsv"),
        Path("lexicons/rusentilex_2017.csv"),
        Path("rusentilex_2017.txt"),
        Path("rusentilex.txt"),
    ]
    file = next((p for p in candidates if p.exists()), None)
    if not file:
        return {}
    lex = {}
    with file.open("r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            parts = re.split(r"\t|;|,|\s{2,}", s)
            w = None
            for x in parts[1:]:
                m = re.search(r"[-+]?\d+(?:\.\d+)?", x)
                if m:
                    try:
                        w = float(m.group(0)); break
                    except:
                        pass
            if w is None:
                j = " ".join(parts[1:]).lower()
                if "pos" in j: w = 1.0
                elif "neg" in j: w = -1.0
            if w is None: 
                continue
            w = float(w)
            lem = lemma((parts[0] if parts else "").strip())
            if lem:
                old = lex.get(lem)
                if old is None or abs(w) > abs(old):
                    lex[lem] = w
    return lex

RU_SENTI = load_local_rusentilex()

POS_BASE = {
    "рекомендовать","советовать","молодец","отлично","отличный","хорошо","хороший","супер","классный",
    "быстро","оперативно","качественно","профессионально","адекватный","доброжелательный","вежливый",
    "приветливый","удобно","понравиться","спасибо","благодарить","лучший","довольный","аккуратно",
    "компетентный","честный","чисто","прекрасный","замечательный","удивительный"
}
NEG_BASE = {
    "плохо","ужасно","ужас","отвратительно","медленно","дорого","обман","развод","хамство","хам",
    "грубость","грубо","неприятно","некомпетентный","косяк","проблема","кошмар","разочарование",
    "задержка","обмануть","врать","лгать","наплевательский","непрофессионально","худший","грязно",
    "отстой","жуть","лохотрон","скотский","мерзкий","мошенник","мошенничество","отвратительно","отвратно"
}

NEGATIONS     = {"не","нет","ни","никогда","ничуть","никак","без"}
INTENSIFIERS  = {"очень","крайне","супер","реально","прям","безумно","чертовски"}
DIMINISHERS   = {"слегка","немного","чуть","чутка","едва","небольшо"}
POS_EMOJI     = {"🙂","😊","😃","😍","👍","🔥","💪","🥳","😄","😎"}
NEG_EMOJI     = {"☹","🙁","😠","😡","👎","💩","😭","😤","😞","😣"}
NEG_TRIGGERS  = {"не советую","не рекомендую","ни в коем случае","никогда не обращайтесь",
                 "полный ужас","полный отстой","развод","лохотрон","мошенник","мошенничество","отвратительно","отвратно"}
POS_TRIGGERS  = {"без проблем","все отлично","всё отлично","очень доволен","очень довольна",
                 "все супер","всё супер"}

NEG_WEIGHT_MULT       = 1.6
NEG_INVERT_POS_MULT   = 1.5
LEX_POS_TH_SHORT      = 0.12
LEX_NEG_TH_SHORT      = -0.10
LEX_POS_TH_LONG       = 0.14
LEX_NEG_TH_LONG       = -0.11

def normalize_text(s: str) -> str:
    s = (s or "").lower().replace("ё","е")
    s = re.sub(r"[^\w\s!?%:+\-]", " ", s, flags=re.U)
    return re.sub(r"\s+", " ", s).strip()

def to_tokens(s: str):
    return [t for t in re.split(r"\s+", s) if t]

def emoji_score(raw: str) -> float:
    p = sum(ch in POS_EMOJI for ch in (raw or ""))
    n = sum(ch in NEG_EMOJI for ch in (raw or ""))
    return float(p - n)

def phrase_flags_and_score(raw: str):
    txt = (raw or "").lower().replace("ё","е")
    toks = re.findall(r"\w+|[!?]+", txt)

    negations = {"не","без","ни"}
    hard_neg = False
    hard_pos = False
    ph_score = 0.0

    multi_neg = ["не советую","не рекомендую","ни в коем случае","никогда не обращайтесь","полный ужас","полный отстой"]
    for ph in multi_neg:
        if ph in txt:
            hard_neg = True

    single_neg = ["развод","лохотрон","мошенник","мошенничество"]
    for i, w in enumerate(toks):
        for trg in single_neg:
            if w.startswith(trg):
                prev = {toks[j] for j in (i-1, i-2) if j >= 0}
                if prev.isdisjoint(negations):
                    hard_neg = True

    for ph in ["без проблем","все отлично","всё отлично","очень доволен","очень довольна","все супер","всё супер"]:
        if ph in txt:
            hard_pos = True

    if re.search(r"\bбез\s+обман\w*\b", txt):
        ph_score += 1.5

    return hard_neg, hard_pos, ph_score


def lex_word_weight(tok: str) -> float:
    lem = lemma(tok)
    if RU_SENTI:
        w = RU_SENTI.get(lem)
        if w is not None:
            return float(w * NEG_WEIGHT_MULT) if w < 0 else float(w)
    if lem in POS_BASE: return 1.0
    if lem in NEG_BASE: return -1.0 * NEG_WEIGHT_MULT
    return 0.0

def lex_score(text: str):
    if not text: return 0.0, 0, 0, 0
    raw = text
    toks = to_tokens(normalize_text(text))
    score = emoji_score(raw)
    pos_hits = 0; neg_hits = 0

    for i, tok in enumerate(toks):
        base = lex_word_weight(tok)
        if base == 0.0: 
            continue
        negated = any(i-k >= 0 and toks[i-k] in NEGATIONS for k in (1,2))
        w = base
        if negated:
            w = -base * (NEG_INVERT_POS_MULT if base > 0 else 1.0)
        for k in (1,2):
            j = i - k
            if j >= 0:
                if toks[j] in INTENSIFIERS: w *= 1.4
                elif toks[j] in DIMINISHERS: w *= 0.65
        score += w

        if (base > 0 and not negated) or (base < 0 and negated):
            pos_hits += 1
        elif (base < 0 and not negated) or (base > 0 and negated):
            neg_hits += 1

    if score != 0:
        score *= 1.0 + min(raw.count("!"), 3)*0.1
    return score, len(toks), pos_hits, neg_hits

def lex_label(score: float, n: int) -> str:
    denom = max(3.0, float(n))
    norm = score / denom
    if n <= 5:
        if norm > LEX_POS_TH_SHORT: return "positive"
        if norm < LEX_NEG_TH_SHORT: return "negative"
    else:
        if norm > LEX_POS_TH_LONG:  return "positive"
        if norm < LEX_NEG_TH_LONG:  return "negative"
    return "neutral"

class NBModel:
    def __init__(self):
        self.pos_counts = Counter()
        self.neg_counts = Counter()
        self.pos_total  = 0
        self.neg_total  = 0
        self.vocab      = set()
        self.pos_docs   = 0
        self.neg_docs   = 0

    def fit_doc(self, text: str, label: str):
        toks = [lemma(t) for t in to_tokens(normalize_text(text))]
        if not toks: return
        for w in toks:
            if label == "pos":
                self.pos_counts[w] += 1; self.pos_total += 1
            elif label == "neg":
                self.neg_counts[w] += 1; self.neg_total += 1
            self.vocab.add(w)
        if label == "pos": self.pos_docs += 1
        elif label == "neg": self.neg_docs += 1

    def ready(self) -> bool:
        return self.pos_docs >= 10 and self.neg_docs >= 10 and len(self.vocab) >= 100

    def predict_llr(self, text: str) -> tuple[float, int]:
        toks = [lemma(t) for t in to_tokens(normalize_text(text))]
        if not toks: return 0.0, 0
        V = max(1, len(self.vocab))
        alpha = 1.0
        prior_pos = math.log((self.pos_docs + 1) / (self.pos_docs + self.neg_docs + 2))
        prior_neg = math.log((self.neg_docs + 1) / (self.pos_docs + self.neg_docs + 2))
        ll_pos = prior_pos
        ll_neg = prior_neg
        for w in toks:
            cp = self.pos_counts.get(w, 0)
            cn = self.neg_counts.get(w, 0)
            ll_pos += math.log((cp + alpha) / (self.pos_total + alpha * V))
            ll_neg += math.log((cn + alpha) / (self.neg_total + alpha * V))
        return (ll_pos - ll_neg), len(toks)

def train_nb_from_rows(rows):
    nb = NBModel()
    for r in rows:
        txt = (r.get(TEXT_COL) or "").strip()
        rating_raw = r.get(RATING_COL)
        if not txt or rating_raw in (None, ""):
            continue
        try:
            rating = float(str(rating_raw).replace(",", "."))
        except:
            continue
        if rating >= 4.0:
            nb.fit_doc(txt, "pos")
        elif rating <= 2.0:
            nb.fit_doc(txt, "neg")
    return nb

def ensemble_label(text: str, nb: NBModel) -> str:
    hard_neg, hard_pos, ph_score = phrase_flags_and_score(text)
    if hard_neg: return "negative"
    if hard_pos: return "positive"

    ls, ln, pos_hits, neg_hits = lex_score(text)
    lex_norm = (ls + ph_score) / max(3.0, float(ln))
    lex_comp = math.tanh(lex_norm)  # [-1,1]
    lex_cls = lex_label(ls + ph_score, ln)

    if not (nb and nb.ready()):
        return lex_cls

    llr, n = nb.predict_llr(text)
    nb_norm = llr / max(3.0, float(n))
    nb_comp = math.tanh(nb_norm)

    fused = 0.6 * nb_comp + 0.4 * lex_comp

    if abs(fused) < 0.08:
        return "neutral"

    if (lex_comp * nb_comp) < 0 and max(abs(lex_comp), abs(nb_comp)) < 0.22:
        return "neutral"

    if (pos_hits + neg_hits) <= 1 and abs(fused) < 0.15:
        return "neutral"

    if fused >= 0.15:
        return "positive"
    if fused <= -0.13:
        return "negative"

    return "neutral"

def process_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        rows = list(rdr)
        fieldnames = list(rdr.fieldnames or [])

    if SENT_COL not in fieldnames:
        fieldnames.append(SENT_COL)

    nb = train_nb_from_rows(rows)

    for r in rows:
        text = (r.get(TEXT_COL) or "").strip()
        r[SENT_COL] = ensemble_label(text, nb)

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)

    print(f"Updated: {path}  ({len(rows)} lines)  "
          f"{'(RuSentiLex: local)' if RU_SENTI else '(built-in dictionary)'}  "
          f"{'(NB: trained)' if nb.ready() else '(NB: not enough data - vocabulary used)'}")

if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DEFAULT_CSV)
    process_csv(p)
