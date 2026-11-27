#!/usr/bin/env python3
"""Build two markdown text datasets: the practical guide PDF and UNHCR Help pages."""

import html
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

PDF_BBOX = Path("/tmp/guida_bbox.html")
OUT_DIR = Path("/Users/danielemargiotta/Documents/PROG_ALE_CONTRATTO/Senza nome/dataset")
GUIDA_OUT = OUT_DIR / "guida_pratica_2026.md"
UNHCR_OUT = OUT_DIR / "unhcr_help_asilo_in_italia.md"
UNHCR_ROOT = "https://help.unhcr.org/italy/it/asilo-in-italia/"
UA = "Mozilla/5.0 (compatible; research-text-dataset/1.0)"


def unescape(text: str) -> str:
    return html.unescape(text).replace("\xa0", " ").replace("\u200b", "")


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def pdf_pages(raw: str):
    return re.findall(r"<page width=\"[\d.]+\" height=\"[\d.]+\">(.*?)</page>", raw, re.S)


def page_words(page: str):
    words = []
    for a, b, c, d, t in re.findall(
        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>',
        page,
    ):
        text = unescape(t).strip()
        if text:
            words.append((float(a), float(b), float(c), float(d), text))
    return words


def cluster_lines(words):
    lines = []
    for w in sorted(words, key=lambda item: (item[1], item[0])):
        height = w[3] - w[1]
        placed = False
        for line in lines:
            ref_y = sum(item[1] for item in line) / len(line)
            ref_h = sum(item[3] - item[1] for item in line) / len(line)
            if abs(w[1] - ref_y) <= 3.6 and abs(height - ref_h) <= 6:
                line.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])
    packed = []
    for line in lines:
        line.sort(key=lambda item: item[0])
        text = " ".join(item[4] for item in line)
        text = re.sub(r"\s+", " ", text).strip()
        y = min(item[1] for item in line)
        x = min(item[0] for item in line)
        height = sum(item[3] - item[1] for item in line) / len(line)
        packed.append({"y": y, "x": x, "text": text, "h": height, "page": 0})
    packed.sort(key=lambda item: item["y"])
    return packed


def is_footer_number(line):
    return bool(re.fullmatch(r"\d{1,2}", line["text"]) and line["y"] > 520 and line["x"] > 300)


def is_marker(text):
    return bool(re.fullmatch(r"\d{1,2}[.)]?|[•\-]", text.strip()))


def attach_markers(lines):
    """Prefix margin numbers to the nearest title, including large phase numbers."""
    consumed = set()
    for i, cur in enumerate(lines):
        if not is_marker(cur["text"]):
            continue
        best = None
        best_d = 14
        for j, other in enumerate(lines):
            if j == i or is_marker(other["text"]):
                continue
            distance = abs(other["y"] - cur["y"])
            if other["page"] != cur["page"]:
                continue
            if distance < best_d and other["x"] + 5 >= cur["x"]:
                best = j
                best_d = distance
        if best is None:
            continue
        prefix = cur["text"]
        if re.fullmatch(r"\d", prefix):
            prefix = f"{prefix}."
        lines[best]["text"] = f"{prefix} {lines[best]['text']}"
        lines[best]["x"] = min(cur["x"], lines[best]["x"])
        consumed.add(i)
    return [line for i, line in enumerate(lines) if i not in consumed]


def postpone_overlays(lines):
    """Keep large all-caps banners from landing inside a body sentence."""
    body = []
    waiting = []
    for line in lines:
        letters = [ch for ch in line["text"] if ch.isalpha()]
        upper_ratio = (sum(ch.isupper() for ch in letters) / len(letters)) if letters else 0
        banner = line["h"] >= 24 and upper_ratio > 0.8 and len(line["text"].split()) <= 6
        if banner and body and body[-1]["page"] == line["page"]:
            prev = body[-1]["text"]
            if not prev.endswith((".", "!", "?", ":")):
                waiting.append(line)
                continue
        body.append(line)
        if waiting and (line["text"].endswith((".", "!", "?")) or line["h"] >= 20):
            body.extend(waiting)
            waiting = []
    body.extend(waiting)
    return body


def line_continues(prev, nxt):
    if nxt["text"][:1].islower():
        return True
    if prev["text"].endswith((",", ";", "(", "–", "—", "/")):
        return True
    if prev["text"].endswith("-") and not prev["text"].endswith(" -"):
        return True
    return False


def caps_wrap(prev, nxt):
    def upper_ratio(value):
        letters = [char for char in value if char.isalpha()]
        if not letters:
            return 0
        return sum(char.isupper() for char in letters) / len(letters)

    if len(prev["text"]) < 35 or prev["text"].endswith((".", "!", "?", ":")):
        return False
    if upper_ratio(prev["text"]) < 0.85:
        return False
    head = nxt["text"].split(":", 1)[0]
    return upper_ratio(head) > 0.85 and len(head.split()) <= 4


def heading_continues(prev, nxt, gap):
    if prev["h"] < 20 or nxt["h"] < 20 or gap > 40:
        return False
    if prev["text"].endswith(("?", "!", ".", ":")):
        return False
    return True


def join_wraps(lines):
    paragraphs = []
    buf = None
    for line in lines:
        if buf is None:
            buf = dict(line)
            continue
        gap = line["y"] - buf["y"] if line["page"] == buf["page"] else 99
        if line_continues(buf, line) and (line["page"] != buf["page"] or gap < 48):
            buf["text"] = f"{buf['text']} {line['text']}"
            buf["y"] = line["y"]
            buf["h"] = max(buf["h"], line["h"])
            continue
        if line["page"] == buf["page"] and gap < 22 and caps_wrap(buf, line):
            buf["text"] = f"{buf['text']} {line['text']}"
            buf["y"] = line["y"]
            buf["h"] = max(buf["h"], line["h"])
            continue
        if line["page"] == buf["page"] and heading_continues(buf, line, gap):
            buf["text"] = f"{buf['text']} {line['text']}"
            buf["y"] = line["y"]
            buf["h"] = max(buf["h"], line["h"])
            continue
        if (
            line["page"] == buf["page"]
            and 0 <= gap < 16
            and buf["h"] < 20
            and line["h"] < 20
            and not is_structural(line["text"])
            and not is_structural(buf["text"])
        ):
            buf["text"] = f"{buf['text']} {line['text']}"
            buf["y"] = line["y"]
            continue
        paragraphs.append(buf)
        buf = dict(line)
    if buf:
        paragraphs.append(buf)
    return paragraphs


def is_structural(text):
    if text in {"INDICE", "Attenzione"}:
        return True
    if re.match(r"^\d+\.\s+[A-ZÀ-Ú]", text) and len(text) < 150:
        return True
    if re.match(r"^\d+\)\s+[A-ZÀ-Ú]", text) and len(text) < 180:
        return True
    if re.match(r"^\d+\s+[A-ZÀ-Ú]", text) and len(text) < 120:
        return True
    if text.endswith("?") and len(text) < 220 and len(text.split()) <= 28:
        return True
    letters = [ch for ch in text if ch.isalpha()]
    if letters and len(text) < 110 and sum(ch.isupper() for ch in letters) / len(letters) > 0.85:
        if text.endswith("."):
            return False
        return True
    return False


def format_index_item(text):
    match = re.match(r"^(\d+)\.\s+(.+?)\s+(\d{1,2})$", text)
    if not match:
        return text
    return f"{match.group(1)}. {match.group(2)} — pag. {match.group(3)}"


def to_markdown_blocks(paragraphs):
    blocks = []
    in_index = False
    for para in paragraphs:
        text = re.sub(r"\s+", " ", para["text"]).strip()
        text = text.replace(" • ", "\n- ").replace("• ", "- ")
        text = text.replace("  ", "\n- ").replace(" ", "- ")
        if text == "INDICE":
            in_index = True
            blocks.append(("h1", "Indice"))
            continue
        if in_index and re.match(r"^1\.\s+Perché questa guida\?$", text):
            in_index = False
        if in_index and re.match(r"^\d+\.\s+", text):
            blocks.append(("index", format_index_item(text)))
            continue
        if (
            para["h"] >= 26
            and re.match(r"^[1-8]\.\s+[A-ZÀ-Ú]", text)
            and len(text.split()) <= 18
            and not text.endswith(".")
        ):
            blocks.append(("h1", text))
            continue
        if re.match(r"^\d+\.\s+[A-ZÀ-Ú]", text) and len(text.split()) <= 16 and not text.endswith("."):
            blocks.append(("h2", text))
            continue
        if text == "Attenzione":
            blocks.append(("h3", "Attenzione"))
            continue
        if para["h"] >= 22 and 1 < len(text.split()) <= 12 and not text.endswith((".", ";", ",")):
            blocks.append(("h2", text))
            continue
        if text.endswith("?") and len(text) < 220 and len(text.split()) <= 28:
            blocks.append(("h2", text))
            continue
        letters = [ch for ch in text if ch.isalpha()]
        upper_ratio = (sum(ch.isupper() for ch in letters) / len(letters)) if letters else 0
        if letters and upper_ratio > 0.85 and len(text) < 110 and not text.endswith("."):
            if len(text.split()) <= 4 and not text.endswith(":"):
                blocks.append(("li", text))
            else:
                blocks.append(("h2", text))
            continue
        if re.match(r"^[-–]\s+", text):
            blocks.append(("li", re.sub(r"^[-–]\s+", "", text)))
            continue
        blocks.append(("p", text))
    return blocks


def render_blocks(blocks):
    lines = [
        "# Guida pratica per richiedenti protezione internazionale in Italia",
        "",
        "Fonte: `CNA_guida_pratica_2026.pdf`.",
        "Copertina: 2026. Aggiornamento indicato nel documento: novembre 2025.",
        "Testo estratto dal PDF conservando capitoli, domande e paragrafi.",
        "Le pagine 2 e 3 non contengono testo estraibile.",
        "La pagina dei Paesi del Regolamento Dublino è una mappa: sotto la spiegazione restano le etichette nell’ordine di lettura.",
        "",
    ]
    list_open = False
    for kind, text in blocks:
        if kind != "li" and kind != "index" and list_open:
            lines.append("")
            list_open = False
        if lines and lines[-1] != "" and kind in {"h1", "h2", "h3"}:
            lines.append("")
        if kind == "h1":
            lines.extend([f"## {text}", ""])
        elif kind == "h2":
            lines.extend([f"### {text}", ""])
        elif kind == "h3":
            lines.extend([f"#### {text}", ""])
        elif kind == "index":
            lines.append(f"- {text}")
        elif kind == "li":
            lines.append(f"- {text}")
            list_open = True
        else:
            # split embedded bullets created inside a paragraph
            parts = text.split("\n")
            first = True
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                if part.startswith("- "):
                    if first:
                        lines.append("")
                    lines.append(part)
                    list_open = True
                else:
                    if list_open:
                        lines.append("")
                        list_open = False
                    lines.extend([part, ""])
                first = False
    if list_open:
        lines.append("")
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    text = re.sub(r"(https?://\S+)-\s+", r"\1-", text)
    text = re.sub(r" \.", ".", text)
    return text


def build_guida():
    raw = PDF_BBOX.read_text(encoding="utf-8")
    all_lines = []
    for number, page in enumerate(pdf_pages(raw), 1):
        if number in {1, 2, 3}:
            continue
        lines = [
            line
            for line in cluster_lines(page_words(page))
            if not is_footer_number(line) and not re.fullmatch(r"[A-Za-zÀ-ú]", line["text"])
            and line["text"] != "essere esaminata nel Paese in cui ti trovi."
            and not line["text"].endswith("tutte le informa")
        ]
        for line in lines:
            line["page"] = number
        lines = attach_markers(lines)
        lines = postpone_overlays(lines)
        all_lines.extend(lines)
    paragraphs = join_wraps(all_lines)
    kept = []
    for para in paragraphs:
        text = re.sub(r"\s+", " ", para["text"]).strip()
        if kept:
            previous = re.sub(r"\s+", " ", kept[-1]["text"]).strip()
            if previous.endswith(text) or previous.endswith(text.rstrip(".")):
                continue
        kept.append(para)
    blocks = to_markdown_blocks(kept)
    GUIDA_OUT.write_text(render_blocks(blocks), encoding="utf-8")


# ---------------------------------------------------------------------------
# UNHCR
# ---------------------------------------------------------------------------

class FragmentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip_div = 0
        self.div_depth = 0
        self.skip_classes = (
            "button-block",
            "topic-listing",
            "content-listing",
            "related-posts",
            "social",
            "wp-block-button",
        )
        self.skip = 0
        self.capture = []
        self.href = None
        self.list_type = []
        self.li_open = False
        self.block_tags = {"p", "div", "h1", "h2", "h3", "h4", "li", "blockquote"}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "svg", "img", "noscript"}:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "div":
            self.div_depth += 1
            classes = attrs.get("class", "")
            if any(token in classes for token in self.skip_classes):
                if self.skip_div == 0:
                    self.skip_div = self.div_depth
                return
        if self.skip_div:
            return
        if tag == "br":
            self.out.append("\n")
        elif tag == "a":
            self.href = attrs.get("href")
        elif tag in {"strong", "b"}:
            self.out.append("**")
        elif tag in {"em", "i"}:
            self.out.append("*")
        elif tag == "li":
            kind = self.list_type[-1] if self.list_type else "ul"
            if kind == "ol":
                count = sum(1 for item in self.out if item == "\nOLITEM\n") + 1
                self.out.append(f"\n{count}. ")
            else:
                self.out.append("\n- ")
            self.li_open = True
        elif tag == "ul":
            self.list_type.append("ul")
        elif tag == "ol":
            self.list_type.append("ol")
        elif tag in {"p", "h2", "h3", "h4", "blockquote"}:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg", "img", "noscript"} and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag == "div":
            if self.skip_div and self.div_depth == self.skip_div:
                self.skip_div = 0
            self.div_depth = max(0, self.div_depth - 1)
        if self.skip_div:
            return
        if tag == "a" and self.href:
            self.href = None
        elif tag in {"strong", "b"}:
            self.out.append("**")
        elif tag in {"em", "i"}:
            self.out.append("*")
        elif tag in {"ul", "ol"} and self.list_type:
            self.list_type.pop()
            self.out.append("\n")
        elif tag in {"p", "h2", "h3", "h4", "blockquote", "div"}:
            self.out.append("\n")
        elif tag == "li":
            self.li_open = False

    def handle_data(self, data):
        if self.skip or self.skip_div:
            return
        text = data.replace("\xa0", " ")
        if text.strip() or self.out:
            self.out.append(text)

    def markdown(self):
        text = "".join(self.out)
        text = text.replace("➡️", "").replace("→", "").replace("•", "\n- ")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"\*\*\s+\*\*", "", text)
        text = re.sub(r"\*{3,}", "**", text)
        lines = []
        for line in text.splitlines():
            clean = re.sub(r"[ \t]{2,}", " ", line).strip()
            clean = re.sub(r"\*\*(\s*)\*\*", "", clean)
            if clean in {"**", "*", "-", "- **"}:
                continue
            lines.append(clean)
        rendered = "\n".join(lines)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
        return rendered


def fragment_to_md(fragment: str) -> str:
    parser = FragmentParser()
    parser.feed(fragment)
    parser.close()
    return parser.markdown()


def decode_cf_email(encoded: str) -> str:
    key = int(encoded[:2], 16)
    return "".join(chr(int(encoded[i : i + 2], 16) ^ key) for i in range(2, len(encoded), 2))


def deobfuscate_emails(page: str) -> str:
    def replace(match):
        return decode_cf_email(match.group(1))

    return re.sub(
        r'<(?:span|a)\b[^>]*data-cfemail="([0-9a-fA-F]+)"[^>]*>.*?</(?:span|a)>',
        replace,
        page,
        flags=re.S,
    )


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=40) as response:
        page = response.read().decode("utf-8", "replace")
    return deobfuscate_emails(page)


def article_html(page: str) -> str:
    match = re.search(r"<article\b.*?</article>", page, re.S)
    return match.group(0) if match else ""


def page_title(article: str) -> str:
    match = re.search(r'<h1 class="entry-title[^"]*">(.*?)</h1>', article, re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", match.group(1)))).strip()


def qa_items(article: str):
    items = []
    pattern = re.compile(
        r'<span class="q-and-a__box-title[^"]*">(.*?)</span>.*?<div class="accordion-body[^"]*"(?:[^>]*)>(.*?)</div>\s*</div>\s*</div>',
        re.S,
    )
    for title_html, body_html in pattern.findall(article):
        title = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", title_html))).strip()
        title = title.replace("➡️", "").replace("→", "").strip()
        body = fragment_to_md(body_html)
        items.append((title, body))
    return items


def topic_links(article: str):
    links = []
    seen = set()
    for href, text in re.findall(r'<a href="([^"]+)"\s*>(.*?)</a>', article, re.S):
        if not href.startswith(UNHCR_ROOT):
            continue
        label = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", text))).strip()
        if not label or href in seen:
            continue
        seen.add(href)
        links.append((label, href if href.endswith("/") else href + "/"))
    return links


def promote_bold_labels(body: str) -> str:
    rendered = []
    for line in body.splitlines():
        match = re.fullmatch(r"\*\*\s*(.+?)\s*\*\*", line.strip())
        label = match.group(1).strip() if match else ""
        if label.endswith("?") or label in {"DIRITTI", "DOVERI"}:
            rendered.extend(["", f"### {label}", ""])
        else:
            rendered.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(rendered)).strip()


def build_unhcr():
    hub = fetch(UNHCR_ROOT)
    hub_article = article_html(hub)
    children = topic_links(hub_article)
    # Menu order is already the listing order. Follow any deeper page under the same path.
    queue = [UNHCR_ROOT] + [href for _, href in children]
    ordered = []
    seen = set()
    pages = {}
    while queue:
        url = queue.pop(0)
        if not url.endswith("/"):
            url += "/"
        if url in seen or not url.startswith(UNHCR_ROOT):
            continue
        seen.add(url)
        ordered.append(url)
        page = fetch(url)
        article = article_html(page)
        pages[url] = article
        for _, href in topic_links(article):
            if href not in seen:
                queue.append(href)

    lines = [
        "# Asilo in Italia — UNHCR Help",
        "",
        f"Fonte: {UNHCR_ROOT}",
        "Raccolta del testo italiano delle pagine e delle domande della sezione, il 11 ottobre 2026.",
        "Ogni pagina resta separata. Le domande seguono l’ordine della pagina.",
        "",
        "## Pagine raccolte",
        "",
    ]
    for url in ordered:
        title = page_title(pages[url]) or url
        lines.append(f"- [{title}]({url})")
    lines.append("")

    for url in ordered:
        article = pages[url]
        title = page_title(article) or url
        lines.extend([f"## {title}", "", f"URL: {url}", ""])
        questions = qa_items(article)
        links = [item for item in topic_links(article) if item[1] != url]
        if questions:
            for index, (question, answer) in enumerate(questions, 1):
                lines.extend([f"### {index}. {question}", ""])
                if answer:
                    lines.extend([answer, ""])
                else:
                    lines.append("")
        elif links and url.rstrip("/") == UNHCR_ROOT.rstrip("/"):
            lines.append("Pagina di snodo. I contenuti sono nelle pagine collegate.")
            lines.append("")
        else:
            # Fallback: visible text of the article without the title repeat.
            body = fragment_to_md(article)
            body = re.sub(rf"^{re.escape(title)}\s*", "", body).strip()
            body = promote_bold_labels(body)
            if body:
                lines.extend([body, ""])
        if links and url.rstrip("/") != UNHCR_ROOT.rstrip("/"):
            lines.append("Pagine collegate da questa scheda:")
            lines.append("")
            for label, href in links:
                lines.append(f"- [{label}]({href})")
            lines.append("")

    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"
    text = text.replace("**Lycamobi**le", "Lycamobile")
    UNHCR_OUT.write_text(text, encoding="utf-8")
    return ordered


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_guida()
    print("guida", GUIDA_OUT, GUIDA_OUT.stat().st_size)
    urls = build_unhcr()
    print("unhcr", UNHCR_OUT, UNHCR_OUT.stat().st_size)
    for url in urls:
        print(" -", url)
