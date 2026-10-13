"""Local bilingual (Italian / English) lexicon for asset search.

No model, no network: a hand-written concept table maps Italian words (with
plural forms) to English concepts, English concepts to the synonyms and
Synty filename tokens that actually occur in pack assets, and quantity
words to numbers. It is deliberately explicit, so a miss is visible and
fixable rather than a silent embedding guess.
"""

from __future__ import annotations

import re
import unicodedata

# English concept -> search terms (first = canonical; the rest are synonyms
# and Synty filename tokens). Keep tokens lower-case.
CONCEPTS: dict[str, list[str]] = {
    "house": ["house", "home", "cottage", "hut", "cabin", "longhouse", "dwelling", "bld", "building"],
    "building": ["building", "bld", "house", "shop", "tower", "structure"],
    "shop": ["shop", "store", "market", "stall", "storefront", "bakery", "blacksmith", "tavern", "inn"],
    "tavern": ["tavern", "inn", "pub", "bar"],
    "castle": ["castle", "keep", "fortress", "fort", "tower", "wall", "gate"],
    "tower": ["tower", "watchtower", "turret"],
    "church": ["church", "chapel", "temple", "cathedral"],
    "temple": ["temple", "shrine", "ruin"],
    "barn": ["barn", "stable", "shed", "silo"],
    "windmill": ["windmill", "mill"],
    "bridge": ["bridge"],
    "wall": ["wall"],
    "fence": ["fence", "palisade", "railing", "gate"],
    "gate": ["gate", "door"],
    "door": ["door", "gate"],
    "window": ["window"],
    "roof": ["roof"],
    "floor": ["floor", "ground", "tile", "plank", "dirt", "platform"],
    "key": ["key", "keys"],
    "lever": ["lever", "switch", "handle"],
    "gold": ["gold", "coin", "coins", "treasure", "gem", "gems"],
    "cage": ["cage", "gibbet", "jail", "prison", "cell"],
    "plate": ["plate", "pressure", "button", "pad"],
    "stairs": ["stairs", "stair", "steps", "staircase", "ladder"],
    "well": ["well", "water_well"],
    "fountain": ["fountain"],
    "tree": ["tree", "pine", "oak", "birch", "palm", "fir", "willow", "trunk"],
    "bush": ["bush", "shrub", "hedge", "plant"],
    "plant": ["plant", "flower", "pot", "bush", "fern"],
    "flower": ["flower", "flowers", "plant"],
    "grass": ["grass", "weed", "reeds"],
    "rock": ["rock", "stone", "boulder", "cliff", "pebble"],
    "mountain": ["mountain", "cliff", "rock", "peak"],
    "log": ["log", "stump", "trunk", "wood"],
    "mushroom": ["mushroom", "fungus"],
    "chair": ["chair", "stool", "seat", "bench", "armchair"],
    "stool": ["stool", "chair", "seat"],
    "bench": ["bench", "seat"],
    "table": ["table", "desk", "counter"],
    "desk": ["desk", "table"],
    "bed": ["bed", "bunk", "cot"],
    "sofa": ["sofa", "couch", "armchair"],
    "shelf": ["shelf", "shelves", "bookcase", "rack", "cabinet"],
    "cabinet": ["cabinet", "cupboard", "wardrobe", "drawer", "shelf"],
    "crate": ["crate", "box", "chest"],
    "box": ["box", "crate", "cardboard"],
    "barrel": ["barrel", "keg", "cask"],
    "chest": ["chest", "treasure", "crate"],
    "sack": ["sack", "bag"],
    "bag": ["bag", "backpack", "sack"],
    "bucket": ["bucket", "pail"],
    "cart": ["cart", "wagon", "wheelbarrow"],
    "lamp": ["lamp", "lantern", "light", "streetlight", "torch"],
    "torch": ["torch", "brazier", "lantern"],
    "candle": ["candle", "candelabra"],
    "fire": ["fire", "campfire", "flame", "brazier", "bonfire"],
    "campfire": ["campfire", "fire", "firepit"],
    "tent": ["tent", "camp", "teepee", "yurt"],
    "sign": ["sign", "signpost", "billboard", "banner", "plaque"],
    "banner": ["banner", "flag"],
    "flag": ["flag", "banner"],
    "statue": ["statue", "monument", "totem"],
    "totem": ["totem", "statue", "pole"],
    "weapon": ["weapon", "wep", "sword", "axe", "spear", "bow", "gun", "rifle", "pistol", "knife", "mace", "hammer"],
    "sword": ["sword", "blade", "wep"],
    "axe": ["axe", "hatchet"],
    "spear": ["spear", "pike", "lance"],
    "bow": ["bow", "crossbow"],
    "shield": ["shield"],
    "helmet": ["helmet", "helm"],
    "gun": ["gun", "rifle", "pistol", "shotgun", "smg"],
    "tool": ["tool", "hammer", "saw", "shovel", "pickaxe", "wrench", "pitchfork"],
    "food": ["food", "bread", "apple", "meat", "cheese", "fish", "fruit", "vegetable"],
    "bottle": ["bottle", "flask", "potion", "jar"],
    "potion": ["potion", "flask", "bottle"],
    "book": ["book", "scroll", "tome"],
    "computer": ["computer", "pc", "laptop", "monitor", "screen"],
    "phone": ["phone", "telephone"],
    "car": ["car", "veh", "vehicle", "sedan", "taxi", "truck", "van"],
    "vehicle": ["vehicle", "veh", "car", "truck", "cart", "bike"],
    "truck": ["truck", "lorry", "van"],
    "boat": ["boat", "ship", "rowboat", "canoe", "raft"],
    "ship": ["ship", "boat", "longship", "galleon", "spaceship"],
    "spaceship": ["spaceship", "ship", "spacecraft", "fighter", "shuttle"],
    "road": ["road", "street", "path", "sidewalk", "pavement"],
    "path": ["path", "road", "trail", "cobble"],
    "dock": ["dock", "pier", "jetty"],
    "cannon": ["cannon", "artillery"],
    "trap": ["trap", "spike", "spikes"],
    "skull": ["skull", "bones", "bone", "skeleton"],
    "bones": ["bones", "bone", "skull"],
    "character": ["character", "chr", "npc", "person", "man", "woman"],
    "npc": ["npc", "character", "chr", "civilian"],
    "man": ["man", "male", "guy", "character"],
    "woman": ["woman", "female", "girl", "lady", "character"],
    "child": ["child", "kid", "boy", "girl"],
    "goblin": ["goblin", "gob"],
    "orc": ["orc"],
    "knight": ["knight", "paladin", "soldier"],
    "soldier": ["soldier", "guard", "warrior", "knight"],
    "guard": ["guard", "soldier"],
    "warrior": ["warrior", "fighter", "soldier", "viking"],
    "wizard": ["wizard", "mage", "sorcerer", "witch"],
    "king": ["king", "queen", "royal"],
    "merchant": ["merchant", "shopkeeper", "trader", "vendor"],
    "farmer": ["farmer", "peasant"],
    "villager": ["villager", "peasant", "townsperson", "civilian"],
    "pirate": ["pirate", "captain", "sailor"],
    "zombie": ["zombie", "undead"],
    "skeleton_enemy": ["skeleton", "undead"],
    "police": ["police", "cop", "officer"],
    "animal": ["animal", "cow", "pig", "horse", "chicken", "sheep", "dog", "cat", "goat", "deer", "wolf", "bear"],
    "horse": ["horse", "pony"],
    "dog": ["dog", "wolf"],
    "cow": ["cow", "bull", "cattle"],
    "chicken": ["chicken", "hen", "rooster"],
    "animation": ["animation", "anim", "idle", "walk", "run", "jump", "attack"],
    "walk": ["walk", "locomotion"],
    "run": ["run", "sprint", "locomotion"],
    "idle": ["idle"],
    "attack": ["attack", "swing", "slash", "punch"],
    "effect": ["fx", "effect", "particle", "vfx"],
    "smoke": ["smoke", "fx"],
    "explosion": ["explosion", "explode", "fx"],
    "magic": ["magic", "spell", "fx"],
    "icon": ["icon", "ui"],
    "ui": ["ui", "hud", "button", "panel", "icon"],
    "hud": ["hud", "ui"],
    "button": ["button", "ui"],
    "skybox": ["skybox", "sky"],
    "terrain": ["terrain", "ground", "plane", "tile"],
    "ground": ["ground", "terrain", "tile", "floor"],
}

# Italian surface form -> English concept. Includes plurals and common variants.
IT: dict[str, str] = {}


def _it(concept: str, *forms: str) -> None:
    for f in forms:
        IT[f] = concept


_it("house", "casa", "case", "casetta", "casette", "abitazione", "abitazioni", "capanna", "capanne", "baita", "baite",
    "cottage", "dimora", "dimore", "villetta", "villette")
_it("building", "edificio", "edifici", "palazzo", "palazzi", "costruzione", "costruzioni", "struttura", "strutture")
_it("shop", "negozio", "negozi", "bottega", "botteghe", "emporio", "mercato", "mercati", "bancarella", "bancarelle",
    "panetteria", "fucina", "fabbro", "macelleria")
_it("tavern", "taverna", "taverne", "locanda", "locande", "osteria", "osterie", "pub")
_it("castle", "castello", "castelli", "fortezza", "fortezze", "forte", "rocca")
_it("tower", "torre", "torri", "torretta", "torrette")
_it("church", "chiesa", "chiese", "cappella", "cattedrale")
_it("temple", "tempio", "templi", "santuario", "rovina", "rovine")
_it("barn", "fienile", "fienili", "stalla", "stalle", "granaio", "capannone")
_it("windmill", "mulino", "mulini")
_it("bridge", "ponte", "ponti")
_it("wall", "muro", "muri", "mura", "parete", "pareti", "muretto")
_it("fence", "recinto", "recinti", "recinzione", "recinzioni", "staccionata", "staccionate", "palizzata", "steccato")
_it("gate", "cancello", "cancelli", "portone")
_it("door", "porta", "porte")
_it("window", "finestra", "finestre")
_it("roof", "tetto", "tetti")
_it("key", "chiave", "chiavi")
_it("lever", "leva", "leve", "levetta", "interruttore")
_it("gold", "oro", "moneta", "monete", "soldi", "gemma", "gemme")
_it("cage", "gabbia", "gabbie", "prigione", "cella", "celle")
_it("plate", "pedana", "pedane", "piastra", "pulsante_a_pressione")
_it("floor", "pavimento", "pavimenti")
_it("stairs", "scala", "scale", "scalinata", "gradini")
_it("well", "pozzo", "pozzi")
_it("fountain", "fontana", "fontane")
_it("tree", "albero", "alberi", "pino", "pini", "quercia", "querce", "betulla", "betulle", "palma", "palme", "abete",
    "abeti")
_it("bush", "cespuglio", "cespugli", "arbusto", "arbusti", "siepe", "siepi")
_it("plant", "pianta", "piante", "vaso", "vasi")
_it("flower", "fiore", "fiori")
_it("grass", "erba", "erbe", "prato")
_it("rock", "roccia", "rocce", "sasso", "sassi", "pietra", "pietre", "masso", "massi", "scoglio", "scogli")
_it("mountain", "montagna", "montagne", "monte", "rupe", "dirupo")
_it("log", "tronco", "tronchi", "ceppo", "ceppi", "legna")
_it("mushroom", "fungo", "funghi")
_it("chair", "sedia", "sedie", "seggiola", "seggiole", "poltrona", "poltrone")
_it("stool", "sgabello", "sgabelli")
_it("bench", "panca", "panche", "panchina", "panchine")
_it("table", "tavolo", "tavoli", "tavola", "tavolino", "tavolini", "bancone", "banconi")
_it("desk", "scrivania", "scrivanie")
_it("bed", "letto", "letti", "branda")
_it("sofa", "divano", "divani")
_it("shelf", "scaffale", "scaffali", "mensola", "mensole", "libreria", "librerie")
_it("cabinet", "armadio", "armadi", "credenza", "mobile", "mobili", "cassettiera")
_it("crate", "cassa", "casse", "cassetta")
_it("box", "scatola", "scatole", "scatolone", "scatoloni")
_it("barrel", "barile", "barili", "botte", "botti")
_it("chest", "forziere", "forzieri", "baule", "bauli", "tesoro")
_it("sack", "sacco", "sacchi")
_it("bag", "borsa", "borse", "zaino", "zaini")
_it("bucket", "secchio", "secchi", "secchiello")
_it("cart", "carro", "carri", "carretto", "carretti", "carriola")
_it("lamp", "lampada", "lampade", "lampione", "lampioni", "lanterna", "lanterne", "luce", "luci")
_it("torch", "torcia", "torce", "fiaccola", "fiaccole", "braciere")
_it("candle", "candela", "candele")
_it("fire", "fuoco", "fuochi", "fiamma", "fiamme")
_it("campfire", "falò", "falo", "fuoco_da_campo")
_it("tent", "tenda", "tende", "accampamento")
_it("sign", "cartello", "cartelli", "insegna", "insegne", "segnale", "segnali")
_it("banner", "stendardo", "stendardi", "striscione")
_it("flag", "bandiera", "bandiere")
_it("statue", "statua", "statue", "monumento", "monumenti")
_it("totem", "totem")
_it("weapon", "arma", "armi")
_it("sword", "spada", "spade")
_it("axe", "ascia", "asce", "accetta")
_it("spear", "lancia", "lance")
_it("bow", "arco", "archi", "balestra")
_it("shield", "scudo", "scudi")
_it("helmet", "elmo", "elmi", "casco")
_it("gun", "pistola", "pistole", "fucile", "fucili")
_it("tool", "attrezzo", "attrezzi", "strumento", "martello", "pala", "piccone", "sega")
_it("food", "cibo", "pane", "mela", "mele", "carne", "formaggio", "pesce", "frutta", "verdura")
_it("bottle", "bottiglia", "bottiglie", "fiasco", "barattolo", "barattoli")
_it("potion", "pozione", "pozioni")
_it("book", "libro", "libri", "pergamena", "pergamene")
_it("computer", "computer", "pc", "monitor", "schermo", "schermi")
_it("phone", "telefono", "telefoni")
_it("car", "auto", "automobile", "automobili", "macchina", "macchine", "taxi")
_it("vehicle", "veicolo", "veicoli", "mezzo", "mezzi")
_it("truck", "camion", "furgone", "furgoni")
_it("boat", "barca", "barche", "zattera", "canoa")
_it("ship", "nave", "navi", "galeone", "galeoni", "drakkar")
_it("spaceship", "astronave", "astronavi", "navicella", "navicelle")
_it("road", "strada", "strade", "via", "vie", "marciapiede")
_it("path", "sentiero", "sentieri", "vialetto")
_it("dock", "molo", "moli", "pontile", "banchina")
_it("cannon", "cannone", "cannoni")
_it("trap", "trappola", "trappole")
_it("skull", "teschio", "teschi")
_it("bones", "osso", "ossa")
_it("character", "personaggio", "personaggi", "persona", "persone", "png", "abitante", "abitanti")
_it("npc", "npc", "comparsa", "comparse")
_it("man", "uomo", "uomini", "ragazzo", "ragazzi")
_it("woman", "donna", "donne", "ragazza", "ragazze", "signora")
_it("child", "bambino", "bambini", "bambina", "bambine", "bimbo", "bimbi")
_it("goblin", "goblin", "folletto", "folletti")
_it("orc", "orco", "orchi")
_it("knight", "cavaliere", "cavalieri", "paladino", "paladini")
_it("soldier", "soldato", "soldati", "milite")
_it("guard", "guardia", "guardie", "sentinella", "sentinelle")
_it("warrior", "guerriero", "guerrieri", "combattente", "combattenti", "lottatore", "lottatori")
_it("wizard", "mago", "maghi", "strega", "streghe", "stregone", "stregoni")
_it("king", "re", "regina", "regine", "principe", "principessa")
_it("merchant", "mercante", "mercanti", "commerciante", "commercianti", "negoziante", "negozianti", "venditore")
_it("farmer", "contadino", "contadini", "contadina", "agricoltore")
_it("villager", "paesano", "paesani", "cittadino", "cittadini", "popolano", "popolani")
_it("pirate", "pirata", "pirati", "marinaio", "marinai", "capitano")
_it("zombie", "zombie", "zombi", "non_morto", "nonmorti")
_it("skeleton_enemy", "scheletro", "scheletri")
_it("police", "polizia", "poliziotto", "poliziotti", "agente")
_it("animal", "animale", "animali")
_it("horse", "cavallo", "cavalli")
_it("dog", "cane", "cani", "lupo", "lupi")
_it("cow", "mucca", "mucche", "toro", "tori")
_it("chicken", "gallina", "galline", "pollo", "polli", "gallo")
_it("animation", "animazione", "animazioni")
_it("walk", "camminata", "camminare", "cammina")
_it("run", "corsa", "correre", "corre")
_it("idle", "inattivo", "fermo")
_it("attack", "attacco", "attacchi", "attaccare")
_it("effect", "effetto", "effetti", "particellare", "particelle")
_it("smoke", "fumo")
_it("explosion", "esplosione", "esplosioni")
_it("magic", "magia", "magie", "incantesimo", "incantesimi")
_it("icon", "icona", "icone")
_it("ui", "interfaccia", "menu")
_it("hud", "hud")
_it("button", "pulsante", "pulsanti", "bottone", "bottoni")
_it("skybox", "cielo")
_it("terrain", "terreno", "terreni", "suolo")
_it("ground", "pavimentazione")

# Modifiers (adjectives). Italian -> English token used for boosting.
MODIFIERS: dict[str, str] = {
    "medievale": "medieval", "medievali": "medieval", "medieval": "medieval",
    "fantasy": "fantasy", "fantastico": "fantasy", "fantastica": "fantasy",
    "moderno": "modern", "moderna": "modern", "moderni": "modern", "moderne": "modern", "modern": "modern",
    "fantascientifico": "scifi", "fantascienza": "scifi", "scifi": "scifi", "sci-fi": "scifi", "futuristico": "scifi",
    "vichingo": "viking", "vichinga": "viking", "vichinghi": "viking", "vichinghe": "viking", "viking": "viking",
    "pirata": "pirate", "piratesco": "pirate",
    "piccolo": "small", "piccola": "small", "piccoli": "small", "piccole": "small", "small": "small",
    "grande": "large", "grandi": "large", "large": "large", "big": "large", "enorme": "large",
    "alto": "tall", "alta": "tall", "alti": "tall", "alte": "tall", "tall": "tall",
    "basso": "short", "bassa": "short",
    "rotto": "broken", "rotta": "broken", "rotti": "broken", "rotte": "broken", "distrutto": "destroyed",
    "distrutta": "destroyed", "broken": "broken", "rovinato": "damaged", "danneggiato": "damaged",
    "vecchio": "old", "vecchia": "old", "antico": "old", "antica": "old", "old": "old",
    "legno": "wood", "ligneo": "wood", "wooden": "wood", "wood": "wood",
    "pietra": "stone", "stone": "stone", "metallo": "metal", "metal": "metal", "ferro": "metal",
    "innevato": "snow", "innevata": "snow", "neve": "snow", "snow": "snow", "invernale": "snow",
    "rosso": "red", "rossa": "red", "blu": "blue", "verde": "green", "giallo": "yellow", "nero": "black",
    "bianco": "white",
    "goblin": "goblin", "zombie": "zombie",
    "doppio": "double", "doppia": "double", "angolo": "corner", "angolare": "corner",
    "interno": "interior", "interni": "interior", "esterno": "exterior",
}

NUMBERS: dict[str, int] = {
    "un": 1, "uno": 1, "una": 1, "un'": 1, "a": 1, "an": 1, "one": 1, "single": 1,
    "due": 2, "two": 2, "paio": 2, "pair": 2, "couple": 2,
    "tre": 3, "three": 3, "quattro": 4, "four": 4, "cinque": 5, "five": 5, "sei": 6, "six": 6,
    "sette": 7, "seven": 7, "otto": 8, "eight": 8, "nove": 9, "nine": 9, "dieci": 10, "ten": 10,
    "undici": 11, "eleven": 11, "dodici": 12, "twelve": 12, "venti": 20, "twenty": 20,
    "alcuni": 3, "alcune": 3, "qualche": 3, "some": 3, "several": 3, "diversi": 4, "diverse": 4,
    "molti": 6, "molte": 6, "many": 6, "tanti": 6, "tante": 6,
}

STOPWORDS = {
    # it
    "mi", "servono", "serve", "voglio", "vorrei", "cerco", "cercare", "trova", "trovami", "dammi", "metti",
    "il", "lo", "la", "i", "gli", "le", "l", "di", "del", "dello", "della", "dei", "degli", "delle", "da",
    "dal", "in", "nel", "nella", "con", "per", "su", "tra", "fra", "e", "ed", "o", "oppure", "anche",
    "che", "un", "uno", "una", "qualcosa", "tipo", "stile", "ho", "bisogno", "usando", "utilizzando", "miei",
    "mie", "mio", "mia", "asset", "synty", "crea", "creare", "fai", "piccola", "città",
    # en
    "need", "want", "find", "me", "get", "give", "the", "of", "with", "for", "and", "or", "some", "to",
    "my", "using", "use", "on", "style", "like", "please", "build", "create", "make",
}
# "piccola"/"città" in the stoplist only when they lead a scene phrase; they
# are still recognised as modifiers/concepts when attached to an item.
STOPWORDS -= {"piccola"}
STOPWORDS = {unicodedata.normalize("NFKD", w).encode("ascii", "ignore").decode() for w in STOPWORDS}

SPLIT_RE = re.compile(r"\s*(?:,|;|\+|\be\b|\bed\b|\band\b|\bplus\b|\bcon\b|\bwith\b|\n)\s*", re.I)
WORD_RE = re.compile(r"[a-zA-Zàèéìòóù0-9'_\-]+")


def fold(text: str) -> str:
    """Lower-case and strip accents (falò -> falo)."""
    t = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _it_lookup(word: str) -> str | None:
    w = fold(word)
    if w in IT:
        return IT[w]
    # Light plural/gender folding: -i/-e -> -o/-a/-e
    for a, b in (("i", "o"), ("i", "e"), ("e", "a"), ("he", "a"), ("hi", "o"), ("ie", "ia")):
        if w.endswith(a):
            cand = w[: -len(a)] + b
            if cand in IT:
                return IT[cand]
    return None


def _en_lookup(word: str) -> str | None:
    w = fold(word)
    if w in CONCEPTS:
        return w
    if w.endswith("ies") and w[:-3] + "y" in CONCEPTS:
        return w[:-3] + "y"
    if w.endswith("es") and w[:-2] in CONCEPTS:
        return w[:-2]
    if w.endswith("s") and w[:-1] in CONCEPTS:
        return w[:-1]
    for concept, terms in CONCEPTS.items():
        if w in terms[1:4]:
            return concept
    return None


def analyse_phrase(phrase: str) -> dict:
    """One requested item: ``"tre case medievali"`` ->
    ``{qty: 3, concept: "house", modifiers: ["medieval"], terms: [...], unknown: []}``."""
    words = [w.strip("'") for w in WORD_RE.findall(phrase)]
    qty = None
    concepts: list[str] = []
    modifiers: list[str] = []
    unknown: list[str] = []
    for w in words:
        f = fold(w)
        if f.isdigit():
            qty = int(f)
            continue
        if f in NUMBERS and qty is None and not concepts:
            qty = NUMBERS[f]
            continue
        c = _it_lookup(f) or _en_lookup(f)
        if c:
            # an adjective-looking word that is also a concept ("goblin") counts once
            if c not in concepts:
                concepts.append(c)
            continue
        if f in MODIFIERS:
            if MODIFIERS[f] not in modifiers:
                modifiers.append(MODIFIERS[f])
            continue
        if f in STOPWORDS or f in NUMBERS or len(f) < 2:
            continue
        unknown.append(f)
    # Head noun = first concept; later concepts qualify it ("personaggio goblin"
    # -> character + goblin, "torre di pietra" -> tower + stone).
    head = concepts[0] if concepts else None
    qualifiers = concepts[1:]
    # "personaggio goblin": the specific creature is the better head.
    if head in ("character", "npc", "man", "woman") and qualifiers:
        head, qualifiers = qualifiers[0], [head] + qualifiers[1:]
    terms: list[str] = []
    if head:
        terms.extend(CONCEPTS[head])
    for q in qualifiers:
        terms.extend(CONCEPTS[q][:3])
    terms.extend(modifiers)
    terms.extend(unknown)  # untranslated words (proper names, English ids) still search
    return {
        "text": phrase.strip(),
        "qty": qty or 1,
        "concept": head,
        "qualifiers": qualifiers,
        "modifiers": modifiers,
        "unknown": unknown,
        "terms": list(dict.fromkeys(t for t in terms if t)),
        "wants_character": head in CHARACTER_CONCEPTS or any(q in CHARACTER_CONCEPTS for q in qualifiers),
        "wants_animation": head in ANIMATION_CONCEPTS,
        "wants_fx": head in FX_CONCEPTS,
        "wants_ui": head in UI_CONCEPTS,
    }


CHARACTER_CONCEPTS = {
    "character", "npc", "man", "woman", "child", "goblin", "orc", "knight", "soldier", "guard", "warrior", "wizard",
    "king", "merchant", "farmer", "villager", "pirate", "zombie", "skeleton_enemy", "police",
}
ANIMATION_CONCEPTS = {"animation", "walk", "run", "idle", "attack"}
FX_CONCEPTS = {"effect", "smoke", "explosion", "magic", "fire"}
UI_CONCEPTS = {"icon", "ui", "hud", "button"}


def split_request(text: str) -> list[str]:
    """Split a multi-item request into item phrases.

    ``"tre case medievali, un pozzo, due alberi e un personaggio goblin"`` ->
    four phrases. A leading ``"Mi servono"`` / ``"I need"`` is dropped by the
    per-phrase stopword filter.
    """
    text = re.sub(r"[.!?]+$", "", text.strip())
    text = re.sub(r"(?i)three\.?js|godot\s*4?|unity|unreal", " ", text)
    parts = [p for p in SPLIT_RE.split(text) if p and p.strip()]
    return parts or [text]


def parse_request(text: str) -> list[dict]:
    items = []
    for phrase in split_request(text):
        a = analyse_phrase(phrase)
        if a["concept"] or a["unknown"] or (a["modifiers"] and items):
            items.append(a)
    # A trailing modifier-only fragment qualifies the previous item.
    merged: list[dict] = []
    for a in items:
        if not a["concept"] and not a["unknown"] and merged:
            merged[-1]["modifiers"] += [m for m in a["modifiers"] if m not in merged[-1]["modifiers"]]
            merged[-1]["terms"] += [m for m in a["modifiers"] if m not in merged[-1]["terms"]]
            continue
        merged.append(a)
    return merged
