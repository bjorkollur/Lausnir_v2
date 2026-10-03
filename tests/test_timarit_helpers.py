# tests/test_timarit_helpers.py
from engine.timarit.article_type import infer_article_type
from engine.timarit.authors import clean_author, looks_like_person, split_authors
from engine.timarit.lang import detect_lang


def test_clean_author_strips_titles_and_roles():
    assert clean_author("Dr. Margrét Einarsdóttir") == ("Margrét Einarsdóttir", "Dr.")
    assert clean_author("Snorri Snorrason hdl.") == ("Snorri Snorrason", "hdl.")
    assert clean_author("Stefán A. Svensson hrl., LL.M.") == ("Stefán A. Svensson", "hrl., LL.M.")
    assert clean_author("Þyrí Halla Steingrímsdóttir, cand. jur. og löglærður fulltrúi hjá DP lögmönnum") == ("Þyrí Halla Steingrímsdóttir", "cand. jur.")
    assert clean_author("PRÓFESSOR ÍSLEIFUR ARNASON") == ("Ísleifur Arnason", "prófessor")
    assert clean_author("Kjartan Bjarni Björgvinsson") == ("Kjartan Bjarni Björgvinsson", None)


def test_split_authors():
    assert split_authors("Arnar Heimir Lárusson og Jörgen Már Ágústsson") == ["Arnar Heimir Lárusson", "Jörgen Már Ágústsson"]
    assert split_authors("A B, C D og E F") == ["A B", "C D", "E F"]
    assert split_authors("Hafsteinn Einarsson") == ["Hafsteinn Einarsson"]


def test_looks_like_person():
    assert looks_like_person("Teitur Gissurarson") and looks_like_person("dr. Hafsteinn Dan Kristjánsson")
    assert not looks_like_person("Um endurupptöku einkamála") and not looks_like_person("Efnisþáttur Tamílamálsins")


def test_infer_article_type():
    assert infer_article_type("Ritdómar", "Bók um kröfurétt", "Fræðigrein") == "Ritdómur"
    assert infer_article_type(None, "Frá ritstjóra", "Fræðigrein") == "Ritstjórnargrein"
    assert infer_article_type(None, "Viðtal við Markús Sigurbjörnsson", "Annað") == "Viðtal"
    assert infer_article_type("Dómareifanir", "Hrd. 2010", "Fræðigrein") == "Dómareifun"
    assert infer_article_type(None, "Hvenær er vanhæfi smitandi?", "Fræðigrein") == "Fræðigrein"
    assert infer_article_type(None, "Aðalfundur LMFÍ 2015", "Annað") == "Annað"


def test_detect_lang():
    assert detect_lang("Dómstóllinn taldi að skilyrði væru ekki uppfyllt og vísaði málinu frá.") == "is"
    assert detect_lang("The court held that the conditions were not met and dismissed the case.") == "en"
    assert detect_lang("x y z", default="is") == "is"


def test_looks_like_person_allows_initials_and_rejects_proper_noun_titles():
    assert looks_like_person("Stefán A. Svensson")
    assert looks_like_person("Dr. Margrét Einarsdóttir")
    assert looks_like_person("PRÓFESSOR ÍSLEIFUR ARNASON")
    assert not looks_like_person("Mannréttindadómstóll Evrópu")
    assert not looks_like_person("Hæstiréttur Íslands")


def test_split_authors_drops_title_only_parts():
    assert split_authors("Eiríkur Tómasson, prófessor") == ["Eiríkur Tómasson"]
    assert split_authors("Stefán A. Svensson hrl., LL.M.") == ["Stefán A. Svensson"]
    assert split_authors("") == []
    assert split_authors(" , ") == []


# --- fix round 1 -------------------------------------------------------------

def test_clean_author_title_words_need_word_boundaries():
    # "llm"/"lm" inside a name must not be taken for the LLM degree
    assert clean_author("Hallmundur Kristinsson") == ("Hallmundur Kristinsson", None)
    assert clean_author("Hjalmar Hallmar") == ("Hjalmar Hallmar", None)
    assert clean_author("Alexandr Jónsson") == ("Alexandr Jónsson", None)


def test_clean_author_more_titles_and_forms():
    assert clean_author("Jón Steinar Gunnlaugsson hæstaréttarlögmaður") == ("Jón Steinar Gunnlaugsson", "hæstaréttarlögmaður")
    assert clean_author("Anna Jónsdóttir héraðsdómslögmaður") == ("Anna Jónsdóttir", "héraðsdómslögmaður")
    assert clean_author("Dr. juris Gunnar G. Schram") == ("Gunnar G. Schram", "Dr. juris")
    assert clean_author("Dr. jur. Gunnar Thoroddsen") == ("Gunnar Thoroddsen", "Dr. jur.")
    assert clean_author("dr. phil. Sigurður Nordal") == ("Sigurður Nordal", "dr. phil.")
    assert clean_author("Guðrún Erlendsdóttir fv. hæstaréttardómari") == ("Guðrún Erlendsdóttir", "fv., hæstaréttardómari")
    assert clean_author("Eiríkur Tómasson fyrrverandi hæstaréttardómari") == ("Eiríkur Tómasson", "fyrrverandi, hæstaréttardómari")
    assert clean_author("Sigurður Líndal prófessor emeritus") == ("Sigurður Líndal", "prófessor, emeritus")
    # a name that merely starts with "Dr" or "Jur" is not a degree
    assert clean_author("Dr. Jurgen Smith") == ("Jurgen Smith", "Dr.")


def test_clean_author_strips_empty_brackets_left_by_titles():
    assert clean_author("Guðrún Jónsdóttir (hdl.)") == ("Guðrún Jónsdóttir", "hdl.")
    assert clean_author("Guðrún Jónsdóttir (hdl., LL.M.)") == ("Guðrún Jónsdóttir", "hdl., LL.M.")


def test_split_authors_case_insensitive_and_comma_og():
    assert split_authors("ARNAR HEIMIR LÁRUSSON OG JÖRGEN MÁR ÁGÚSTSSON") == ["Arnar Heimir Lárusson", "Jörgen Már Ágústsson"]
    assert split_authors("Ágúst Þór Árnason, dósent, og Björg Thorarensen, prófessor") == ["Ágúst Þór Árnason", "Björg Thorarensen"]
    assert split_authors("ANNA-KRISTÍN JÓNSDÓTTIR OG PÉTUR PÉTURSSON") == ["Anna-Kristín Jónsdóttir", "Pétur Pétursson"]


def test_split_authors_drops_role_fragments():
    assert split_authors("Davíð Þór Björgvinsson, dómari við Mannréttindadómstól Evrópu") == ["Davíð Þór Björgvinsson"]
    assert split_authors("Sigurður Líndal, prófessor emeritus") == ["Sigurður Líndal"]
    assert split_authors("Eiríkur Tómasson, fyrrverandi hæstaréttardómari") == ["Eiríkur Tómasson"]
    assert split_authors("Þyrí Halla Steingrímsdóttir, cand. jur. og löglærður fulltrúi hjá DP lögmönnum") == ["Þyrí Halla Steingrímsdóttir"]
    assert split_authors("Páll Hreinsson hrl. og Björg Thorarensen prófessor") == ["Páll Hreinsson", "Björg Thorarensen"]
    # a family-name author and a particle name survive next to a second author
    assert split_authors("Sigurður Líndal og Ármann Snævarr") == ["Sigurður Líndal", "Ármann Snævarr"]
    assert split_authors("Otto von Gierke og Jón Jónsson") == ["Otto von Gierke", "Jón Jónsson"]


def test_split_authors_falls_back_to_cleaned_whole_string():
    # nothing passes the shape check, but the input had text -> keep the cleaned whole
    assert split_authors("Ritstjórn Úlfljóts") == ["Ritstjórn Úlfljóts"]
    assert split_authors("hrl.") == []


def test_looks_like_person_strict_vs_lenient():
    for name in ("Sigurður Líndal", "Ármann Snævarr", "Páll Briem", "Hannes Hafstein", "Gunnar G. Schram", "Jónas Haralz", "Ólafur Thors"):
        assert not looks_like_person(name), name
        assert looks_like_person(name, strict=False), name
    for name in ("Efnisþáttur Tamílamálsins", "Um endurupptöku einkamála"):
        assert not looks_like_person(name)
    assert not looks_like_person("Um endurupptöku einkamála", strict=False)
    assert not looks_like_person("dómari við Mannréttindadómstól Evrópu", strict=False)
    assert not looks_like_person("prófessor", strict=False)
    assert not looks_like_person("", strict=False)
    assert looks_like_person("Ólafur Jóhannesson") and looks_like_person("Ólafur Jóhannesson", strict=False)


def test_swap_decision_with_lenient_author_check():
    """Task 11 swaps title/author when `looks_like_person(title) and not looks_like_person(author, strict=False)`."""
    def swaps(title: str, author: str) -> bool:
        return looks_like_person(title) and not looks_like_person(author, strict=False)

    # correct rows: a person-shaped title (a review of a person's work) and a family-name author
    assert not swaps("Ólafur Jóhannesson", "Sigurður Líndal")
    assert not swaps("Gaukur Jörundsson", "Ármann Snævarr")
    # genuinely swapped row: person in the title column, topic in the author column
    assert swaps("Teitur Gissurarson", "Um endurupptöku einkamála")


def test_infer_article_type_scholarly_titles_are_not_news_or_reports():
    assert infer_article_type(None, "Fréttaflutningur og friðhelgi einkalífs", "Fræðigrein") == "Fræðigrein"
    assert infer_article_type(None, "Dagskrárvald forseta", "Fræðigrein") == "Fræðigrein"
    assert infer_article_type(None, "Tilkynningarskylda lögmanna", "Fræðigrein") == "Fræðigrein"
    assert infer_article_type(None, "Dómar Hæstaréttar um jafnræðisreglu", "Fræðigrein") == "Fræðigrein"
    assert infer_article_type(None, "Aðalfundur LMFÍ 2015", "Annað") == "Annað"


def test_infer_article_type_section_rules():
    assert infer_article_type("Fræðigreinar", "Dagskrárvald forseta", "Annað") == "Fræðigrein"
    assert infer_article_type("Greinar", "Frá ritstjóra", "Annað") == "Fræðigrein"
    assert infer_article_type("Ritgerðir", "Viðtal við einhvern", "Annað") == "Fræðigrein"
    assert infer_article_type("Ritstjórnargreinar", "Titill", "Annað") == "Ritstjórnargrein"
    assert infer_article_type("Fréttir", "Titill", "Fræðigrein") == "Frétt"
    assert infer_article_type("Dómar Hæstaréttar", "Titill", "Fræðigrein") == "Dómareifun"
    # a given section that matches no rule -> the source default, the title is not consulted
    assert infer_article_type("Efnisyfirlit", "Viðtal við Markús", "Annað") == "Annað"
    # empty section behaves like None
    assert infer_article_type("", "Viðtal við Markús", "Annað") == "Viðtal"


def test_infer_article_type_title_rules_are_anchored():
    assert infer_article_type(None, "Frá ritstjóra", "Fræðigrein") == "Ritstjórnargrein"
    assert infer_article_type(None, "Leiðari", "Fræðigrein") == "Ritstjórnargrein"
    assert infer_article_type(None, "Viðtöl við dómara", "Fræðigrein") == "Viðtal"
    assert infer_article_type(None, "Ritdómur um bókina Kröfuréttur", "Fræðigrein") == "Ritdómur"
    assert infer_article_type(None, "Dómareifanir 2014", "Fræðigrein") == "Dómareifun"
    assert infer_article_type(None, "Fréttir af félagsstarfi", "Fræðigrein") == "Frétt"
    assert infer_article_type(None, "Frétt", "Fræðigrein") == "Frétt"
    assert infer_article_type(None, "Dagskrá ráðstefnunnar", "Fræðigrein") == "Frétt"
    assert infer_article_type(None, "Tilkynning", "Fræðigrein") == "Frétt"


# --- fix round 2 -------------------------------------------------------------

def test_split_authors_keeps_nordic_and_foreign_coauthors():
    first = "Jón Jónsson"
    for other in ("Hans Müller", "Jørgen Albæk Jensen", "Patrick O'Brien", "Patrick O’Brien", "Ian McDonald",
                  "Fiona MacLeod", "Åsa Johansson", "Petr Čech", "Hans-Peter Müller", "Sigríður Á. Andersen"):
        assert split_authors(f"{first} og {other}") == [first, other], other
        assert split_authors(f"{other} og {first}") == [other, first], other
    assert split_authors("A. Smith, Hans Müller og Åsa Johansson") == ["A. Smith", "Hans Müller", "Åsa Johansson"]


def test_split_authors_foreign_names_in_capitals_and_nfd():
    import unicodedata
    assert split_authors("JÓN JÓNSSON OG PATRICK O'BRIEN") == ["Jón Jónsson", "Patrick O'Brien"]
    assert split_authors("JÓN JÓNSSON OG HANS-PETER MÜLLER") == ["Jón Jónsson", "Hans-Peter Müller"]
    # decomposed (macOS) input must not lose the second author either
    assert len(split_authors(unicodedata.normalize("NFD", "Jón Jónsson og Hans Müller"))) == 2


def test_split_authors_role_and_lowercase_fragments_still_dropped():
    assert split_authors("Jón Jónsson, dómari við Mannréttindadómstól Evrópu og Hans Müller") == ["Jón Jónsson", "Hans Müller"]
    assert split_authors("Jón Jónsson, prófessor emeritus og Hans Müller") == ["Jón Jónsson", "Hans Müller"]
    assert split_authors("Jón Jónsson, fyrrverandi hæstaréttardómari") == ["Jón Jónsson"]
    assert split_authors("Jón Jónsson, löglærður fulltrúi hjá DP lögmönnum") == ["Jón Jónsson"]
    assert split_authors("Jón Jónsson, vinur og félagi") == ["Jón Jónsson"]
    assert split_authors("Jón Jónsson, dómari við héraðsdóm") == ["Jón Jónsson"]
    # a single-token co-author is not a name by the shape check and stays dropped (accepted limitation)
    assert split_authors("Þórður og Jón Jónsson") == ["Jón Jónsson"]


def test_looks_like_person_unicode_letters():
    for name in ("Hans Müller", "Jørgen Albæk Jensen", "Patrick O'Brien", "Ian McDonald", "Åsa Johansson", "Petr Čech", "Hans-Peter Müller"):
        assert looks_like_person(name, strict=False), name
    assert looks_like_person("Jørgen Albæk Jensen") and looks_like_person("Åsa Johansson")
    assert not looks_like_person("Hans Müller")  # strict: no patronymic ending
    assert not looks_like_person("hans müller", strict=False)
    assert not looks_like_person("Hans müller Müller", strict=False)


def test_clean_author_icelandic_emerítus():
    assert clean_author("Sigurður Líndal prófessor emerítus") == ("Sigurður Líndal", "prófessor, emerítus")
    assert split_authors("Sigurður Líndal, prófessor emerítus") == ["Sigurður Líndal"]
