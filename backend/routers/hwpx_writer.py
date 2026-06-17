"""회의록을 HWPX(한컴 오픈 표준 OWPML, ZIP+XML)로 생성.

구형 .hwp(바이너리)는 파이썬으로 생성할 라이브러리가 없어, 한컴이 여는
오픈 표준 .hwpx 를 직접 조립한다. 최소 구조(version/header/section/package)를
스펙에 맞춰 생성하며, 본문은 안건별 정리 텍스트를 문단으로 넣는다.
"""
import zipfile
from io import BytesIO
from xml.sax.saxutils import escape
from datetime import timezone, timedelta

from routers.doc_content import item_sections

KST = timezone(timedelta(hours=9))


def _para(text: str, bold: bool = False, heading: bool = False) -> str:
    """문단 1개 XML. bold/heading이면 다른 charPr/paraPr id 사용."""
    char_id = "1" if (bold or heading) else "0"
    para_id = "1" if heading else "0"
    return (
        f'<hp:p paraPrIDRef="{para_id}" styleIDRef="0" pageBreak="0" '
        f'columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="{char_id}"><hp:t>{escape(text)}</hp:t></hp:run>'
        f'</hp:p>'
    )


def _build_section(meeting, items) -> str:
    paras = []

    # 첫 문단: 섹션 속성(secPr) + 제목
    sec_pr = (
        '<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" '
        'tabStop="8000" tabStopVal="4000" tabStopUnit="HWPUNIT" '
        'outlineShapeIDRef="1" memoShapeIDRef="0" textVerticalWidthHead="0" '
        'masterPageCnt="0">'
        '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0" '
        'strtnum="0"/>'
        '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
        '<hp:visibility hideFirstHeader="0" hideFirstFooter="0" '
        'hideFirstMasterPage="0" border="SHOW_ALL" fill="SHOW_ALL" '
        'hideFirstPageNum="0" hideFirstEmptyLine="0" showLineNumber="0"/>'
        '<hp:pagePr landscape="WIDELY" width="59528" height="84188" '
        'gutterType="LEFT_ONLY">'
        '<hp:margin header="4252" footer="4252" gutter="0" left="8504" '
        'right="8504" top="5668" bottom="4252"/>'
        '</hp:pagePr>'
        '<hp:footNotePr><hp:autoNumFormat type="DIGIT" userChar="" '
        'prefixChar="" suffixChar=")" supscript="0"/>'
        '<hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/>'
        '<hp:noteSpacing betweenNotes="850" belowLine="567" aboveLine="850"/>'
        '<hp:numbering type="CONTINUOUS" newNum="1"/>'
        '<hp:placement place="EACH_COLUMN" beneathText="0"/></hp:footNotePr>'
        '<hp:endNotePr><hp:autoNumFormat type="DIGIT" userChar="" '
        'prefixChar="" suffixChar=")" supscript="0"/>'
        '<hp:noteLine length="14692344" type="SOLID" width="0.12 mm" '
        'color="#000000"/>'
        '<hp:noteSpacing betweenNotes="0" belowLine="567" aboveLine="850"/>'
        '<hp:numbering type="CONTINUOUS" newNum="1"/>'
        '<hp:placement place="END_OF_DOCUMENT" beneathText="0"/></hp:endNotePr>'
        '<hp:pageBorderFill type="BOTH" borderFillIDRef="1" textBorder="PAPER" '
        'headerInside="0" footerInside="0" fillArea="PAPER">'
        '<hp:offset left="1417" right="1417" top="1417" bottom="1417"/>'
        '</hp:pageBorderFill></hp:secPr>'
    )
    title = escape(meeting.title or "회의록")
    paras.append(
        f'<hp:p paraPrIDRef="1" styleIDRef="0" pageBreak="0" columnBreak="0" '
        f'merged="0"><hp:run charPrIDRef="1">{sec_pr}'
        f'<hp:t>{title}</hp:t></hp:run></hp:p>'
    )

    # 날짜 / 참석자
    if meeting.created_at:
        created = meeting.created_at.replace(tzinfo=timezone.utc).astimezone(KST)
        paras.append(_para(f"📅 {created.strftime('%Y-%m-%d %H:%M')}"))
    if meeting.participants:
        paras.append(_para(f"👥 참석자: {meeting.participants}"))
    paras.append(_para(""))

    # 안건별 정리
    for item in items:
        paras.append(_para(f"{item.order}. {item.agenda}", heading=True))
        for label, body in item_sections(item):
            if isinstance(body, list):
                paras.append(_para(f"[{label}]", bold=True))
                for b in body:
                    paras.append(_para(f"  • {b}"))
            else:
                paras.append(_para(f"[{label}]", bold=True))
                paras.append(_para(f"  {body}"))
        paras.append(_para(""))

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
        'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
        'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
        + "".join(paras) +
        '</hs:sec>'
    )


# 글꼴: 7개 언어 모두 같은 폰트 1개로 정의
def _fontfaces() -> str:
    langs = ["HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER"]
    blocks = []
    for lang in langs:
        blocks.append(
            f'<hh:fontface lang="{lang}" fontCnt="1">'
            f'<hh:font id="0" face="함초롬바탕" type="TTF" isEmbedded="0">'
            f'<hh:typeInfo familyType="FONT_TYPE_UNKNOWN" weight="0" '
            f'proportion="0" contrast="0" strokeVariation="0" armStyle="0" '
            f'letterform="0" midline="0" xHeight="0"/></hh:font></hh:fontface>'
        )
    return f'<hh:fontfaces itemCnt="{len(langs)}">' + "".join(blocks) + '</hh:fontfaces>'


def _font_ref() -> str:
    return ('<hh:fontRef hangul="0" latin="0" hanja="0" japanese="0" '
            'other="0" symbol="0" user="0"/>')


def _ratio() -> str:
    return ('<hh:ratio hangul="100" latin="100" hanja="100" japanese="100" '
            'other="100" symbol="100" user="100"/>')


def _spacing() -> str:
    return ('<hh:spacing hangul="0" latin="0" hanja="0" japanese="0" '
            'other="0" symbol="0" user="0"/>')


def _relsz() -> str:
    return ('<hh:relSz hangul="100" latin="100" hanja="100" japanese="100" '
            'other="100" symbol="100" user="100"/>')


def _offset() -> str:
    return ('<hh:offset hangul="0" latin="0" hanja="0" japanese="0" '
            'other="0" symbol="0" user="0"/>')


def _char_pr(cid: str, height: int, bold: bool) -> str:
    bold_tag = "<hh:bold/>" if bold else ""
    return (
        f'<hh:charPr id="{cid}" height="{height}" textColor="#000000" '
        f'shadeColor="none" useFontSpace="0" useKerning="0" symMark="NONE" '
        f'borderFillIDRef="2">'
        + _font_ref() + _ratio() + _spacing() + _relsz() + _offset()
        + bold_tag +
        '</hh:charPr>'
    )


def _build_header() -> str:
    border_fills = (
        '<hh:borderFills itemCnt="2">'
        '<hh:borderFill id="1" threeD="0" shadow="0" centerLine="NONE" '
        'breakCellSeparateLine="0">'
        '<hh:slash type="NONE" Crooked="0" isCounter="0"/>'
        '<hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
        '<hh:leftBorder type="NONE" width="0.1 mm" color="#000000"/>'
        '<hh:rightBorder type="NONE" width="0.1 mm" color="#000000"/>'
        '<hh:topBorder type="NONE" width="0.1 mm" color="#000000"/>'
        '<hh:bottomBorder type="NONE" width="0.1 mm" color="#000000"/>'
        '<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/>'
        '</hh:borderFill>'
        '<hh:borderFill id="2" threeD="0" shadow="0" centerLine="NONE" '
        'breakCellSeparateLine="0">'
        '<hh:slash type="NONE" Crooked="0" isCounter="0"/>'
        '<hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
        '<hh:leftBorder type="NONE" width="0.1 mm" color="#000000"/>'
        '<hh:rightBorder type="NONE" width="0.1 mm" color="#000000"/>'
        '<hh:topBorder type="NONE" width="0.1 mm" color="#000000"/>'
        '<hh:bottomBorder type="NONE" width="0.1 mm" color="#000000"/>'
        '<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/>'
        '</hh:borderFill>'
        '</hh:borderFills>'
    )

    char_props = (
        '<hh:charProperties itemCnt="2">'
        + _char_pr("0", 1000, False)
        + _char_pr("1", 1100, True)
        + '</hh:charProperties>'
    )

    def para_pr(pid: str, align: str) -> str:
        return (
            f'<hh:paraPr id="{pid}" tabPrIDRef="0" condense="0" '
            f'fontLineHeight="0" snapToGrid="1" suppressLineNumbers="0" '
            f'checked="0">'
            f'<hh:align horizontal="{align}" vertical="BASELINE"/>'
            f'<hh:heading type="NONE" idRef="0" level="0"/>'
            f'<hh:breakSetting breakLatinWord="KEEP_WORD" '
            f'breakNonLatinWord="BREAK_WORD" widowOrphan="0" keepWithNext="0" '
            f'keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/>'
            f'<hh:autoSpacing eAsianEng="0" eAsianNum="0"/>'
            f'<hh:switch><hh:case hp:editableForm="" hp:visible="" '
            f'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            f'<hh:margin><hc:intent value="0" unit="HWPUNIT"/>'
            f'<hc:left value="0" unit="HWPUNIT"/>'
            f'<hc:right value="0" unit="HWPUNIT"/>'
            f'<hc:prev value="0" unit="HWPUNIT"/>'
            f'<hc:next value="0" unit="HWPUNIT"/></hh:margin>'
            f'</hh:case><hh:default>'
            f'<hh:margin><hc:intent value="0" unit="HWPUNIT"/>'
            f'<hc:left value="0" unit="HWPUNIT"/>'
            f'<hc:right value="0" unit="HWPUNIT"/>'
            f'<hc:prev value="0" unit="HWPUNIT"/>'
            f'<hc:next value="0" unit="HWPUNIT"/></hh:margin>'
            f'</hh:default></hh:switch>'
            f'<hh:lineSpacing type="PERCENT" value="160" unit="HWPUNIT"/>'
            f'<hh:border borderFillIDRef="2" offsetLeft="0" offsetRight="0" '
            f'offsetTop="0" offsetBottom="0" connect="0" '
            f'ignoreMargin="0"/></hh:paraPr>'
        )

    para_props = (
        '<hh:paraProperties itemCnt="2">'
        + para_pr("0", "JUSTIFY")
        + para_pr("1", "LEFT")
        + '</hh:paraProperties>'
    )

    styles = (
        '<hh:styles itemCnt="1">'
        '<hh:style id="0" type="PARA" name="바탕글" engName="Normal" '
        'paraPrIDRef="0" charPrIDRef="0" nextStyleIDRef="0" langID="1042" '
        'lockForm="0"/></hh:styles>'
    )

    tab_props = (
        '<hh:tabProperties itemCnt="1">'
        '<hh:tabPr id="0" autoTabLeft="0" autoTabRight="0"/>'
        '</hh:tabProperties>'
    )

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
        'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
        'version="1.4" secCnt="1">'
        '<hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" '
        'equation="1"/>'
        '<hh:refList>'
        + _fontfaces() + border_fills + char_props + tab_props
        + para_props + styles +
        '</hh:refList></hh:head>'
    )


_VERSION_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" '
    'tagetApplication="WORDPROCESSOR" major="5" minor="1" micro="1" '
    'buildNumber="0" os="1" xmlVersion="1.4" application="MeetDocs" '
    'appVersion="1.0.0"/>'
)

_CONTAINER_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<ocf:rootfiles>'
    '<ocf:rootfile full-path="Contents/content.hpf" '
    'media-type="application/hwpml-package+xml"/>'
    '</ocf:rootfiles></ocf:container>'
)

_MANIFEST_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
    'version="1.4">'
    '<odf:file-entry odf:full-path="Contents/header.xml" '
    'odf:media-type="application/xml"/>'
    '<odf:file-entry odf:full-path="Contents/section0.xml" '
    'odf:media-type="application/xml"/>'
    '</odf:manifest>'
)


def _content_hpf(title: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<hpf:HWPApplicationSetting '
        'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" '
        'xmlns:opf="http://www.idpf.org/2007/opf/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<opf:package version="1.4" unique-identifier="meetdocs" '
        'id="meetdocs">'
        '<opf:metadata>'
        f'<opf:title>{escape(title)}</opf:title>'
        '<opf:language>ko</opf:language>'
        '</opf:metadata>'
        '<opf:manifest>'
        '<opf:item id="header" href="Contents/header.xml" '
        'media-type="application/xml"/>'
        '<opf:item id="section0" href="Contents/section0.xml" '
        'media-type="application/xml"/>'
        '</opf:manifest>'
        '<opf:spine>'
        '<opf:itemref idref="header" linear="yes"/>'
        '<opf:itemref idref="section0" linear="yes"/>'
        '</opf:spine>'
        '</opf:package></hpf:HWPApplicationSetting>'
    )


def _prv_text(meeting, items) -> str:
    lines = [meeting.title or "회의록"]
    for item in items:
        lines.append(f"{item.order}. {item.agenda}")
    return "\n".join(lines)


def build_hwpx(meeting, items) -> bytes:
    """회의 + 안건 항목으로 .hwpx 바이트 생성."""
    title = meeting.title or "회의록"
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # mimetype은 압축 없이 첫 번째로
        z.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/hwp+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        z.writestr("version.xml", _VERSION_XML)
        z.writestr("META-INF/container.xml", _CONTAINER_XML)
        z.writestr("META-INF/manifest.xml", _MANIFEST_XML)
        z.writestr("Contents/content.hpf", _content_hpf(title))
        z.writestr("Contents/header.xml", _build_header())
        z.writestr("Contents/section0.xml", _build_section(meeting, items))
        z.writestr("Preview/PrvText.txt", _prv_text(meeting, items))
    return buf.getvalue()
