import argparse
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape


EMU_PER_INCH = 914400
SLIDE_W = 13_333_333
SLIDE_H = 7_500_000


def para_xml(text: str, size: int = 2000, bold: bool = False) -> str:
    text = escape(text)
    b = ' b="1"' if bold else ""
    return (
        f'<a:p><a:r><a:rPr lang="zh-CN" sz="{size}"{b}/>'
        f"<a:t>{text}</a:t></a:r><a:endParaRPr lang=\"zh-CN\" sz=\"{size}\"/></a:p>"
    )


def textbox_xml(shape_id: int, name: str, x: int, y: int, cx: int, cy: int, paragraphs: list[str]) -> str:
    tx = "".join(paragraphs)
    return f"""
    <p:sp>
      <p:nvSpPr>
        <p:cNvPr id="{shape_id}" name="{escape(name)}"/>
        <p:cNvSpPr txBox="1"/>
        <p:nvPr/>
      </p:nvSpPr>
      <p:spPr>
        <a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
        <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
        <a:noFill/>
        <a:ln><a:noFill/></a:ln>
      </p:spPr>
      <p:txBody>
        <a:bodyPr wrap="square" anchor="t"/>
        <a:lstStyle/>
        {tx}
      </p:txBody>
    </p:sp>
    """


def picture_xml(shape_id: int, name: str, rel_id: str, x: int, y: int, cx: int, cy: int) -> str:
    return f"""
    <p:pic>
      <p:nvPicPr>
        <p:cNvPr id="{shape_id}" name="{escape(name)}"/>
        <p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr>
        <p:nvPr/>
      </p:nvPicPr>
      <p:blipFill>
        <a:blip r:embed="{rel_id}"/>
        <a:stretch><a:fillRect/></a:stretch>
      </p:blipFill>
      <p:spPr>
        <a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
        <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
      </p:spPr>
    </p:pic>
    """


def make_slide_xml(title: str, body_items: list[dict], notes: str | None = None) -> str:
    shape_id = 2
    shapes = [
        textbox_xml(
            shape_id,
            "Title",
            500000,
            200000,
            12000000,
            700000,
            [para_xml(title, 2800, True)],
        )
    ]
    shape_id += 1
    for item in body_items:
        if item["type"] == "textbox":
            paras = [para_xml(p["text"], p.get("size", 1800), p.get("bold", False)) for p in item["paras"]]
            shapes.append(
                textbox_xml(shape_id, item["name"], item["x"], item["y"], item["cx"], item["cy"], paras)
            )
            shape_id += 1
        elif item["type"] == "picture":
            shapes.append(
                picture_xml(shape_id, item["name"], item["rel_id"], item["x"], item["y"], item["cx"], item["cy"])
            )
            shape_id += 1

    notes_xml = ""
    if notes:
        notes_xml = f"<p:extLst><p:ext uri=\"{{ABCD}}\"><p14:creationId xmlns:p14=\"http://schemas.microsoft.com/office/powerpoint/2010/main\" val=\"123456789\"/></p:ext></p:extLst>"

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr>
        <p:cNvPr id="1" name=""/>
        <p:cNvGrpSpPr/>
        <p:nvPr/>
      </p:nvGrpSpPr>
      <p:grpSpPr>
        <a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm>
      </p:grpSpPr>
      {''.join(shapes)}
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
  {notes_xml}
</p:sld>
"""


def make_slide_rels_xml(image_rels: list[tuple[str, str]]) -> str:
    rels = [
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
    ]
    for rel_id, target in image_rels:
        rels.append(
            f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/{escape(target)}"/>'
        )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {''.join(rels)}
</Relationships>
"""


def build_presentation(output_path: Path, root: Path) -> None:
    results = root / "results"
    images = {
        "vadds_overview": results / "fusion_sweep_singlev1_multii_vadds_overview_20260321.png",
        "gelu_orig_dag": results / "GeLU_poly_dag.png",
        "gelu_i16_dag": results / "GeLU_poly_split_only_I16_dag.png",
        "gelu_i64_p1_dag": results / "GeLU_poly_split_only_I64_penalized_dag.png",
        "gelu_i64_p05_dag": results / "GeLU_poly_split_only_I64_penalty05_dag.png",
        "gelu_i64_dag": results / "GeLU_poly_split_only_I64_penalized_dag.png",
    }

    i16 = json.loads((results / "GeLU_poly_split_only_I16_optimized_meta.json").read_text(encoding="utf-8"))
    i64_p1 = json.loads((results / "GeLU_poly_split_only_I64_penalized_meta.json").read_text(encoding="utf-8"))
    i64_p05 = json.loads((results / "GeLU_poly_split_only_I64_penalty05_meta.json").read_text(encoding="utf-8"))
    i64_p025 = json.loads((results / "GeLU_poly_split_only_I64_penalty025_meta.json").read_text(encoding="utf-8"))

    slides: list[dict] = []

    slides.append(
        {
            "title": "VF Loop Split Optimization Strategy",
            "body": [
                {
                    "type": "textbox",
                    "name": "subtitle",
                    "x": 800000,
                    "y": 1200000,
                    "cx": 11000000,
                    "cy": 2200000,
                    "paras": [
                        {"text": "目标：针对 GeLU_poly 等 VF 算子，只做第一步 loop 切分优化。", "size": 2200},
                        {"text": "重点考虑：强 mem_bar、UB=256KB、VADDS 实验启发、cut penalty 校正。", "size": 2200},
                        {"text": "生成时间：2026-03-21", "size": 1800},
                    ],
                }
            ],
        }
    )

    slides.append(
        {
            "title": "Problem Setting",
            "body": [
                {
                    "type": "textbox",
                    "name": "problem",
                    "x": 700000,
                    "y": 1000000,
                    "cx": 11800000,
                    "cy": 5000000,
                    "paras": [
                        {"text": "输入：单个 loop 的 VF trace。输出：切分后的多 top-level loop 程序。", "size": 2200},
                        {"text": "切分后会自动插入跨 partition 的 VST / VLD，并在 loop 之间插入 mem_bar(VST_VLD)。", "size": 2200},
                        {"text": "目标函数仍然是 partitioned program 的 cycles，但必须显式考虑边界开销与 UB 压力。", "size": 2200},
                        {"text": "当前 simulator 默认采用 strong whole-loop mem_bar。", "size": 2200},
                    ],
                }
            ],
        }
    )

    slides.append(
        {
            "title": "VADDS Sweep Inspiration",
            "body": [
                {
                    "type": "textbox",
                    "name": "vadds_text",
                    "x": 650000,
                    "y": 900000,
                    "cx": 4000000,
                    "cy": 1800000,
                    "paras": [
                        {"text": "single V1 + ping-pong UB 模型显示甜点区会随 I 移动。", "size": 1900},
                        {"text": "I 越大，越倾向更短的每段依赖链。", "size": 1900},
                        {"text": "这说明 GeLU_poly 的 cut 也必须是 iteration-aware 的。", "size": 1900},
                    ],
                },
                {
                    "type": "picture",
                    "name": "vadds_overview",
                    "rel_id": "rId2",
                    "x": 5000000,
                    "y": 900000,
                    "cx": 7600000,
                    "cy": 5200000,
                },
            ],
        }
    )

    slides.append(
        {
            "title": "Hardware Constraints",
            "body": [
                {
                    "type": "textbox",
                    "name": "constraints",
                    "x": 700000,
                    "y": 1000000,
                    "cx": 11800000,
                    "cy": 5000000,
                    "paras": [
                        {"text": "1. mem_bar 为 whole-loop 语义：后续 loop 的 VLD 要等前一 loop 的 store phase 完成。", "size": 2100},
                        {"text": "2. UB 总大小 256KB，仅用于存放 mem 数据。", "size": 2100},
                        {"text": "3. 每个 VST/VLD 处理 64 个 fp32，因此单个向量占 64*4=256B。", "size": 2100},
                        {"text": "4. 中间值需要做 slot 复用：前一个值生命期结束后，slot 可以被后续边界值复用。", "size": 2100},
                    ],
                }
            ],
        }
    )

    slides.append(
        {
            "title": "GeLU_poly Structure",
            "body": [
                {
                    "type": "textbox",
                    "name": "gelu_structure",
                    "x": 650000,
                    "y": 900000,
                    "cx": 3600000,
                    "cy": 4500000,
                    "paras": [
                        {"text": "原始 DAG 可以分成三段：", "size": 2100, "bold": True},
                        {"text": "前缀：VLD / scaling / clamp / V3", "size": 1900},
                        {"text": "中段：V4 与 V5 两条 Horner-like 长链", "size": 1900},
                        {"text": "尾段：VDIV / +1 / 乘回 V1 / VST", "size": 1900},
                        {"text": "策略：前缀谨慎切，中段按 I 增大而更积极切，尾部避免碎片化。", "size": 1900},
                    ],
                },
                {
                    "type": "picture",
                    "name": "gelu_orig_dag",
                    "rel_id": "rId2",
                    "x": 4700000,
                    "y": 900000,
                    "cx": 7600000,
                    "cy": 5200000,
                },
            ],
        }
    )

    slides.append(
        {
            "title": "Split-Only Optimization Flow",
            "body": [
                {
                    "type": "textbox",
                    "name": "flow",
                    "x": 700000,
                    "y": 900000,
                    "cx": 11800000,
                    "cy": 5200000,
                    "paras": [
                        {"text": "Stage A: 从 trace 构建 DAG，计算 forward depth / width / reverse depth / live-out。", "size": 2050},
                        {"text": "Stage B: 生成 partitioned JSON，插入 VST/VLD，分配可复用 mem_inter_slot。", "size": 2050},
                        {"text": "Stage C: 估计 UB usage，若超过 256KB 则拒绝或重罚。", "size": 2050},
                        {"text": "Stage D: 在 strong mem_bar 模型下跑 simulator。", "size": 2050},
                        {"text": "Stage E: 用 hill-climb 做 add / remove / shift / merge cut 的局部搜索。", "size": 2050},
                        {"text": "额外修正：cut penalty 用来纠正 simulator 偏向过细切分的问题。", "size": 2050},
                    ],
                }
            ],
        }
    )

    slides.append(
        {
            "title": "GeLU_poly Results",
            "body": [
                {
                    "type": "textbox",
                    "name": "result_table",
                    "x": 650000,
                    "y": 850000,
                    "cx": 11800000,
                    "cy": 1800000,
                    "paras": [
                        {"text": f"I=16 baseline: 599 cycles; split-only best: {i16['cycles']} cycles; cuts={i16['best_cuts']}", "size": 1900},
                        {"text": f"I=64 penalty=1.0: {i64_p1['cycles']} cycles; cuts={i64_p1['best_cuts']}", "size": 1900},
                        {"text": f"I=64 penalty=0.5: {i64_p05['cycles']} cycles; cuts={i64_p05['best_cuts']}", "size": 1900},
                        {"text": f"I=64 penalty=0.25: {i64_p025['cycles']} cycles; cuts={i64_p025['best_cuts']}", "size": 1900},
                    ],
                },
                {
                    "type": "picture",
                    "name": "gelu_i16_dag",
                    "rel_id": "rId2",
                    "x": 650000,
                    "y": 2300000,
                    "cx": 5600000,
                    "cy": 3700000,
                },
                {
                    "type": "picture",
                    "name": "gelu_i64_dag",
                    "rel_id": "rId3",
                    "x": 6600000,
                    "y": 2300000,
                    "cx": 5600000,
                    "cy": 3700000,
                },
            ],
        }
    )

    slides.append(
        {
            "title": "Penalty Tuning And Current Takeaways",
            "body": [
                {
                    "type": "textbox",
                    "name": "takeaways",
                    "x": 700000,
                    "y": 900000,
                    "cx": 11800000,
                    "cy": 5200000,
                    "paras": [
                        {"text": "实测反馈：I=64 时，penalty=1.0 / 0.5 / 0.25 差距不大，但 1.0 最好。", "size": 2100},
                        {"text": "说明真实硬件更偏向保守切分：更少的 loop、更完整的尾部。", "size": 2100},
                        {"text": "simulator 至少抓对了方向：不应该把 GeLU_poly 尾部切得过碎。", "size": 2100},
                        {"text": "下一步建议：I 越大，默认 penalty 越强；同时继续用实测去校准 chain-length sweet spot。", "size": 2100},
                    ],
                }
            ],
        }
    )

    media_map: dict[Path, str] = {}
    media_bytes: dict[str, bytes] = {}
    next_media_idx = 1

    slide_xmls: list[str] = []
    slide_rels: list[str] = []
    for slide in slides:
        rels: list[tuple[str, str]] = []
        rel_counter = 2
        body_items = []
        for item in slide["body"]:
            if item["type"] == "picture":
                src = images[item["name"]]
                if src not in media_map:
                    media_name = f"image{next_media_idx}{src.suffix.lower()}"
                    next_media_idx += 1
                    media_map[src] = media_name
                    media_bytes[media_name] = src.read_bytes()
                else:
                    media_name = media_map[src]
                new_item = dict(item)
                new_item["rel_id"] = f"rId{rel_counter}"
                rels.append((new_item["rel_id"], media_name))
                rel_counter += 1
                body_items.append(new_item)
            else:
                body_items.append(item)
        slide_xmls.append(make_slide_xml(slide["title"], body_items))
        slide_rels.append(make_slide_rels_xml(rels))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
  <Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/>
  <Override PartName="/ppt/viewProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"/>
  <Override PartName="/ppt/tableStyles.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
""" + "".join(
        f'  <Override PartName="/ppt/slides/slide{i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>\n'
        for i in range(len(slides))
    ) + "</Types>\n"

    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""

    presentation_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldMasterIdLst>
    <p:sldMasterId id="2147483648" r:id="rId1"/>
  </p:sldMasterIdLst>
  <p:sldIdLst>
""" + "".join(
        f'    <p:sldId id="{256+i}" r:id="rId{i+2}"/>\n' for i in range(len(slides))
    ) + f"""  </p:sldIdLst>
  <p:sldSz cx="{SLIDE_W}" cy="{SLIDE_H}"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>
"""

    pres_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>
""" + "".join(
        f'  <Relationship Id="rId{i+2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i+1}.xml"/>\n'
        for i in range(len(slides))
    ) + """  <Relationship Id="rId20" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/presProps" Target="presProps.xml"/>
  <Relationship Id="rId21" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/viewProps" Target="viewProps.xml"/>
  <Relationship Id="rId22" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/tableStyles" Target="tableStyles.xml"/>
</Relationships>
"""

    slide_master = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
             xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
             xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld name="Office Theme">
    <p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
    </p:spTree>
  </p:cSld>
  <p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
  <p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst>
  <p:txStyles/>
</p:sldMaster>
"""

    slide_master_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
</Relationships>
"""

    slide_layout = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
             xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
             xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1">
  <p:cSld name="Blank">
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>
"""

    slide_layout_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>
"""

    theme_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme">
  <a:themeElements>
    <a:clrScheme name="Office">
      <a:dk1><a:srgbClr val="000000"/></a:dk1>
      <a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>
      <a:dk2><a:srgbClr val="1F497D"/></a:dk2>
      <a:lt2><a:srgbClr val="EEECE1"/></a:lt2>
      <a:accent1><a:srgbClr val="4F81BD"/></a:accent1>
      <a:accent2><a:srgbClr val="C0504D"/></a:accent2>
      <a:accent3><a:srgbClr val="9BBB59"/></a:accent3>
      <a:accent4><a:srgbClr val="8064A2"/></a:accent4>
      <a:accent5><a:srgbClr val="4BACC6"/></a:accent5>
      <a:accent6><a:srgbClr val="F79646"/></a:accent6>
      <a:hlink><a:srgbClr val="0000FF"/></a:hlink>
      <a:folHlink><a:srgbClr val="800080"/></a:folHlink>
    </a:clrScheme>
    <a:fontScheme name="Office">
      <a:majorFont><a:latin typeface="Aptos Display"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>
      <a:minorFont><a:latin typeface="Aptos"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>
    </a:fontScheme>
    <a:fmtScheme name="Office">
      <a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>
      <a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>
      <a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>
      <a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>
    </a:fmtScheme>
  </a:themeElements>
  <a:objectDefaults/>
  <a:extraClrSchemeLst/>
</a:theme>
"""

    pres_props = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentationPr xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>
"""
    view_props = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:viewPr xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>
"""
    table_styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:tblStyleLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>
"""
    app_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application>
  <PresentationFormat>Widescreen</PresentationFormat>
  <Slides>8</Slides>
  <Notes>0</Notes>
  <HiddenSlides>0</HiddenSlides>
  <MMClips>0</MMClips>
  <ScaleCrop>false</ScaleCrop>
  <HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Slides</vt:lpstr></vt:variant><vt:variant><vt:i4>8</vt:i4></vt:variant></vt:vector></HeadingPairs>
  <TitlesOfParts><vt:vector size="8" baseType="lpstr"><vt:lpstr>Title</vt:lpstr><vt:lpstr>Problem Setting</vt:lpstr><vt:lpstr>VADDS Sweep Inspiration</vt:lpstr><vt:lpstr>Hardware Constraints</vt:lpstr><vt:lpstr>GeLU_poly Structure</vt:lpstr><vt:lpstr>Split-Only Optimization Flow</vt:lpstr><vt:lpstr>GeLU_poly Results</vt:lpstr><vt:lpstr>Penalty Tuning</vt:lpstr></vt:vector></TitlesOfParts>
</Properties>
"""
    core_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dc="http://purl.org/dc/elements/1.1/"
                   xmlns:dcterms="http://purl.org/dc/terms/"
                   xmlns:dcmitype="http://purl.org/dc/dcmitype/"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Fusion Split Optimization Strategy</dc:title>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>
"""

    tmp_dir = output_path.with_suffix("")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    try:
        (tmp_dir / "_rels").mkdir()
        (tmp_dir / "docProps").mkdir()
        (tmp_dir / "ppt" / "_rels").mkdir(parents=True)
        (tmp_dir / "ppt" / "slides" / "_rels").mkdir(parents=True)
        (tmp_dir / "ppt" / "slideMasters" / "_rels").mkdir(parents=True)
        (tmp_dir / "ppt" / "slideLayouts" / "_rels").mkdir(parents=True)
        (tmp_dir / "ppt" / "theme").mkdir(parents=True)
        (tmp_dir / "ppt" / "media").mkdir(parents=True)

        (tmp_dir / "[Content_Types].xml").write_text(content_types, encoding="utf-8")
        (tmp_dir / "_rels" / ".rels").write_text(root_rels, encoding="utf-8")
        (tmp_dir / "docProps" / "app.xml").write_text(app_xml, encoding="utf-8")
        (tmp_dir / "docProps" / "core.xml").write_text(core_xml, encoding="utf-8")
        (tmp_dir / "ppt" / "presentation.xml").write_text(presentation_xml, encoding="utf-8")
        (tmp_dir / "ppt" / "_rels" / "presentation.xml.rels").write_text(pres_rels, encoding="utf-8")
        (tmp_dir / "ppt" / "presProps.xml").write_text(pres_props, encoding="utf-8")
        (tmp_dir / "ppt" / "viewProps.xml").write_text(view_props, encoding="utf-8")
        (tmp_dir / "ppt" / "tableStyles.xml").write_text(table_styles, encoding="utf-8")
        (tmp_dir / "ppt" / "slideMasters" / "slideMaster1.xml").write_text(slide_master, encoding="utf-8")
        (tmp_dir / "ppt" / "slideMasters" / "_rels" / "slideMaster1.xml.rels").write_text(slide_master_rels, encoding="utf-8")
        (tmp_dir / "ppt" / "slideLayouts" / "slideLayout1.xml").write_text(slide_layout, encoding="utf-8")
        (tmp_dir / "ppt" / "slideLayouts" / "_rels" / "slideLayout1.xml.rels").write_text(slide_layout_rels, encoding="utf-8")
        (tmp_dir / "ppt" / "theme" / "theme1.xml").write_text(theme_xml, encoding="utf-8")

        for idx, xml in enumerate(slide_xmls, start=1):
            (tmp_dir / "ppt" / "slides" / f"slide{idx}.xml").write_text(xml, encoding="utf-8")
            (tmp_dir / "ppt" / "slides" / "_rels" / f"slide{idx}.xml.rels").write_text(slide_rels[idx - 1], encoding="utf-8")

        for media_name, blob in media_bytes.items():
            (tmp_dir / "ppt" / "media" / media_name).write_bytes(blob)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in tmp_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(tmp_dir).as_posix())
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="fusion_split_strategy_demo.pptx")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    build_presentation(root / args.output, root)
    print(root / args.output)


if __name__ == "__main__":
    main()
