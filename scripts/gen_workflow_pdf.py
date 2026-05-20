# -*- coding: utf-8 -*-
"""生成项目完整工作流 PDF 文档"""
import sys
from pathlib import Path
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 字体注册（macOS 内置中文字体）
_HEITI = "/System/Library/Fonts/STHeiti Medium.ttc"
_HIRAGINO = "/System/Library/Fonts/Hiragino Sans GB.ttc"

for name, path, idx in [
    ("Heiti", _HEITI, 0),
    ("HeitiB", _HEITI, 0),
    ("Hiragino", _HIRAGINO, 0),
]:
    try:
        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=idx))
    except Exception:
        pass

FONT = "Heiti"
FONT_B = "Heiti"

# ── 颜色
C_NAVY   = colors.HexColor("#0d3460")
C_BLUE   = colors.HexColor("#1a5276")
C_LIGHT  = colors.HexColor("#2e86c1")
C_GREEN  = colors.HexColor("#1e8449")
C_ORANGE = colors.HexColor("#ca6f1e")
C_RED    = colors.HexColor("#922b21")
C_GRAY   = colors.HexColor("#666666")
C_TEXT   = colors.HexColor("#1a1a1a")
C_LIGHT_BG  = colors.HexColor("#eaf4fb")
C_GREEN_BG  = colors.HexColor("#e9f7ef")
C_ORANGE_BG = colors.HexColor("#fef9e7")
HDR_BG   = colors.HexColor("#1a5276")
ROW_ALT  = colors.HexColor("#f2f8fc")

PAGE_W, PAGE_H = A4
MARGIN = 2.0 * cm


def styles():
    base = dict(fontName=FONT, leading=18, textColor=C_TEXT)
    bold = dict(fontName=FONT_B)
    return {
        "h1": ParagraphStyle("h1", fontSize=22, spaceAfter=12, spaceBefore=20,
                             textColor=C_NAVY, **bold),
        "h2": ParagraphStyle("h2", fontSize=15, spaceAfter=8, spaceBefore=16,
                             textColor=C_BLUE, **bold),
        "h3": ParagraphStyle("h3", fontSize=12, spaceAfter=6, spaceBefore=10,
                             textColor=C_LIGHT, **bold),
        "body": ParagraphStyle("body", fontSize=10, spaceAfter=4,
                               fontName=FONT, leading=18, textColor=C_TEXT),
        "code": ParagraphStyle("code", fontSize=9, spaceAfter=3,
                               fontName="Courier", textColor=C_NAVY,
                               backColor=colors.HexColor("#f4f6f7"),
                               leftIndent=16, leading=14),
        "bullet": ParagraphStyle("bullet", fontSize=10, spaceAfter=3,
                                 leftIndent=20, bulletIndent=8,
                                 fontName=FONT, leading=18, textColor=C_TEXT),
        "note": ParagraphStyle("note", fontSize=9, textColor=C_GRAY,
                               leftIndent=16, spaceAfter=4,
                               fontName=FONT, leading=14),
        "tag_ok":   ParagraphStyle("tag_ok",   fontSize=9, textColor=C_GREEN,  **bold),
        "tag_warn": ParagraphStyle("tag_warn", fontSize=9, textColor=C_ORANGE, **bold),
        "tag_err":  ParagraphStyle("tag_err",  fontSize=9, textColor=C_RED,    **bold),
    }


def hr(color=C_BLUE, thickness=0.8):
    return HRFlowable(width="100%", thickness=thickness, color=color, spaceAfter=6, spaceBefore=2)


def code_block(text, s):
    lines = text.strip().split("\n")
    items = []
    for line in lines:
        items.append(Paragraph(line.replace(" ", "&nbsp;").replace("<", "&lt;").replace(">", "&gt;"), s["code"]))
    return items


def table(data, col_widths, header_row=True):
    usable = PAGE_W - 2 * MARGIN
    if col_widths and sum(col_widths) < 1.5:
        col_widths = [w * usable for w in col_widths]

    t = Table(data, colWidths=col_widths, repeatRows=1 if header_row else 0)
    cmds = [
        ("FONTNAME",    (0, 0), (-1, -1), FONT),
        ("FONTSIZE",    (0, 0), (-1, -1), 9),
        ("LEADING",     (0, 0), (-1, -1), 14),
        ("ALIGN",       (0, 0), (-1, -1), "LEFT"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("GRID",        (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
    ]
    if header_row:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), HDR_BG),
            ("FONTNAME",   (0, 0), (-1, 0), FONT_B),
            ("FONTSIZE",   (0, 0), (-1, 0), 10),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ]
        for i in range(1, len(data)):
            bg = ROW_ALT if i % 2 == 0 else colors.white
            cmds.append(("BACKGROUND", (0, i), (-1, i), bg))
    t.setStyle(TableStyle(cmds))
    return t


def build(output_path: Path):
    s = styles()
    doc = SimpleDocTemplate(
        str(output_path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="A股量化交易系统 — 完整工作流",
        author="quant_trading",
    )

    story = []

    # ════════════════════════════════════════════
    # 封面
    # ════════════════════════════════════════════
    story += [
        Spacer(1, 3 * cm),
        Paragraph("A 股量化交易系统", ParagraphStyle(
            "cover_title", fontName=FONT_B, fontSize=28,
            textColor=C_NAVY, alignment=1, leading=40)),
        Spacer(1, 0.4 * cm),
        Paragraph("完整工作流文档", ParagraphStyle(
            "cover_sub", fontName=FONT, fontSize=16,
            textColor=C_BLUE, alignment=1, leading=24)),
        Spacer(1, 0.6 * cm),
        hr(C_LIGHT, 2),
        Spacer(1, 0.4 * cm),
        Paragraph(f"文档日期：{date.today().strftime('%Y-%m-%d')}", ParagraphStyle(
            "cover_date", fontName=FONT, fontSize=11,
            textColor=C_GRAY, alignment=1)),
        Spacer(1, 2 * cm),
    ]

    cover_info = [
        ["项目", "A股LightGBM三分类量化选股系统"],
        ["技术栈", "Python 3.11 · LightGBM · Ridge · Pandas · akshare · reportlab"],
        ["数据来源", "通达信本地日线（5644只A股）· 北向资金 · 东方财富 · 同花顺THS"],
        ["预测目标", "未来5日截面三分类（涨/震荡/跌），持仓Top-50，5日调仓"],
        ["模型IC", "测试集 0.020（M3框架无退步，新因子待数据采集后提升）"],
    ]
    story.append(table(cover_info, [0.22, 0.78], header_row=False))
    story.append(PageBreak())

    # ════════════════════════════════════════════
    # 1. 系统架构
    # ════════════════════════════════════════════
    story += [Paragraph("1. 系统架构总览", s["h1"]), hr()]
    story.append(Paragraph(
        "系统分为四个阶段：数据采集 → 特征工程 → 模型训练 → 回测与扫描。"
        "各阶段通过 Parquet 文件解耦，支持按需独立运行或全量重建。", s["body"]))
    story.append(Spacer(1, 0.3 * cm))

    arch = [
        ["层次", "模块", "输入 → 输出"],
        ["数据采集", "TDXCollector / NorthboundCollector\nFundamentalsCollector / FundFlowCollector\nReportCollector / SignalCollector",
         "TDX .day 文件 / akshare API\n→ data/raw/*.parquet"],
        ["特征工程", "assembler.py\nindicators.py / report.py / signal.py\nlabel.py / preprocessing.py",
         "data/raw/ → data/processed/\nmarket_features.parquet（647万行×107列）"],
        ["模型训练", "trainer.py（LightGBM + Ridge 集成）\nevaluator.py",
         "market_features.parquet\n→ data/models/lgbm_model.txt 等"],
        ["回测/扫描", "backtest/engine.py\nbacktest/metrics.py",
         "data/models/ + data/processed/\n→ data/backtest/report.html"],
    ]
    story.append(table(arch, [0.18, 0.35, 0.47]))
    story.append(Spacer(1, 0.5 * cm))

    story += [Paragraph("目录结构", s["h3"])]
    story += code_block("""
data/
├── raw/
│   ├── kline/{code}.parquet          ← TDX K线（5644只）
│   ├── northbound.parquet            ← 北向资金历史（2672条）
│   ├── lhb/YYYY-MM-DD.parquet        ← 龙虎榜日报（M3，待采集）
│   ├── reports/{code}.parquet        ← 研报历史（M3，待采集）
│   └── eps/{code}.parquet            ← EPS预测（M3，待采集）
├── fundamentals/{code}.parquet
├── fund_flow/{code}.parquet
├── processed/
│   ├── market_features.parquet       ← 全市场特征主表
│   └── {code}.parquet                ← 单股缓存（加速增量更新）
├── models/                           ← 模型文件（lgbm/ridge/weights）
├── backtest/                         ← 回测报告 + 选股CSV
└── watermark.json                    ← 增量更新水位记录
""", s)

    story.append(PageBreak())

    # ════════════════════════════════════════════
    # 2. Phase 0 数据准备
    # ════════════════════════════════════════════
    story += [Paragraph("2. Phase 0：数据准备（一次性建库）", s["h1"]), hr()]

    story += [Paragraph("2.1 同步通达信 K 线", s["h2"])]
    story.append(Paragraph(
        "从通达信导出当日快照压缩包，解压并核验文件名日期与实际最大交易日一致后写入水位文件。", s["body"]))
    story += code_block("""
# 同步并验证（文件名格式：hsjday-YYYY-MM-DD.zip）
python main.py sync --zip /path/to/hsjday-2026-05-18.zip

# 转换 TDX .day 文件 → data/raw/kline/*.parquet
python main.py collect

# 可选：同时采集腾讯财经 PE/PB 快照
python main.py collect --with-tencent
""", s)

    story += [Paragraph("2.2 拉取补充数据", s["h2"])]
    supp = [
        ["数据类型", "命令", "耗时", "输出路径"],
        ["基本面（PE/PB/市值）", "python main.py fetch-fund", "1-2 小时", "data/fundamentals/"],
        ["个股主力资金流向", "python main.py fetch-flow", "40-60 分钟", "data/fund_flow/"],
        ["北向资金（自动）", "collect 步骤已包含", "< 1 分钟", "data/raw/northbound.parquet"],
        ["龙虎榜历史（M3）", "SignalCollector.fetch_all()", "按日数量", "data/raw/lhb/"],
        ["研报数量（M3）", "ReportCollector mode=report", "约 2 小时", "data/raw/reports/"],
        ["EPS预测（M3）", "ReportCollector mode=eps", "约 2 小时", "data/raw/eps/"],
    ]
    story.append(table(supp, [0.28, 0.28, 0.16, 0.28]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        "⚠  M3 新增因子（龙虎榜/研报/EPS）在采集前全部为 NaN，模型仍可运行但无增量信号。", s["note"]))

    story.append(PageBreak())

    # ════════════════════════════════════════════
    # 3. Phase 1 特征工程
    # ════════════════════════════════════════════
    story += [Paragraph("3. Phase 1：特征工程", s["h1"]), hr()]
    story += code_block("""
python main.py 1               # 全量重建（~20分钟，5644只股票）
python main.py 1 --sample 100  # 调试模式，仅处理前100只
""", s)

    story += [Paragraph("3.1 assembler.py 流程", s["h2"])]
    story.append(Paragraph(
        "每只股票独立计算，结果缓存至 data/processed/{code}.parquet，"
        "重跑时自动跳过（use_cache=True）。全市场合并后执行截面标签和因子预处理。", s["body"]))

    asm_steps = [
        ["步骤", "函数", "说明"],
        ["① 读K线", "_merge_fundamentals()\n_merge_fund_flow()", "合并基本面、资金流向到K线DataFrame"],
        ["② 技术因子", "add_all_features()", "58个技术指标，见3.2节"],
        ["③ 研报因子", "add_report_features()", "report_count_30d, analyst_count,\neps_consensus_cur, eps_revision"],
        ["④ 信号因子", "add_signal_features()", "lhb_net_buy_30d, lhb_count_30d,\nnorth_net_5d, north_net_trend"],
        ["⑤ 截面标签", "label.py", "截面前30%=涨(2), 后30%=跌(0), 中间=震荡(1)"],
        ["⑥ 预处理", "preprocessing.py", "MAD去极值(±5σ) → 截面中性化 → NaN均值填充"],
    ]
    story.append(table(asm_steps, [0.12, 0.28, 0.60]))

    story += [Paragraph("3.2 技术因子清单（indicators.py）", s["h2"])]
    indicators = [
        ["分类", "因子列"],
        ["均线", "ma5/10/20/60_ratio（收盘价偏离均线比例）"],
        ["EMA", "ema12, ema26（中间量，供MACD使用）"],
        ["MACD", "macd_dif, macd_dea, macd_hist, macd_cross, macd_positive, macd_hist_slope"],
        ["RSI", "rsi, rsi_oversold（<30）, rsi_overbought（>70）"],
        ["KDJ", "kdj_k, kdj_d, kdj_j, kdj_cross（+2金叉/-2死叉）"],
        ["布林带", "boll_width（带宽）, boll_pct（位置百分比）, boll_breakout（上轨突破）"],
        ["ATR", "atr（真实波幅）, atr_ratio（相对价格）"],
        ["量能", "vol_ratio（量比）, vol_trend（短期量趋势）"],
        ["多周期收益", "ret1/3/5/10/20/60/120（1~120日收益率）"],
        ["波动率", "volatility5, volatility20（滚动收益率标准差）"],
        ["价格结构", "high_low_ratio, open_close_ratio, upper_shadow, lower_shadow,\namplitude_ma5, log_price"],
        ["衍生动量", "momentum_quality（ret20/volatility20）, reversal（-ret3）"],
        ["量能情绪", "price_vol_agree（价量配合）, up_streak（连涨天数≤10）"],
        ["均线形态", "bull_score（多头排列0~3分）"],
        ["目标", "future_ret（未来5日收益）, label（截面三分类，流水线填充）"],
    ]
    story.append(table(indicators, [0.18, 0.82]))

    story += [Paragraph("3.3 M3 新增因子（report.py + signal.py）", s["h2"])]
    m3_factors = [
        ["因子", "计算逻辑", "防前视机制"],
        ["report_count_30d", "30交易日内该股研报数量（滚动窗口）", "available_date = publish_date + 1日历天"],
        ["analyst_count", "30交易日内覆盖分析师数（研报去重）", "同上"],
        ["eps_consensus_cur", "最新EPS一致预期（向前填充）", "merge_asof(direction=backward)"],
        ["eps_revision", "EPS预期变化方向（+1上调/-1下调/0不变）", "drop_duplicates保证唯一快照"],
        ["lhb_net_buy_30d", "30交易日内龙虎榜净买入额累计", "available_date = lhb_date + 1日历天"],
        ["lhb_count_30d", "30交易日内龙虎榜上榜次数", "同上，pd.bdate_range确保交易日窗口"],
        ["north_net_5d", "北向资金近5日滚动累计净流入", "（迁移自indicators.py，逻辑不变）"],
        ["north_net_trend", "北向资金5日/20日均值比", "（迁移自indicators.py，逻辑不变）"],
    ]
    story.append(table(m3_factors, [0.22, 0.45, 0.33]))

    story.append(PageBreak())

    # ════════════════════════════════════════════
    # 4. Phase 2 模型训练
    # ════════════════════════════════════════════
    story += [Paragraph("4. Phase 2：模型训练", s["h1"]), hr()]
    story += code_block("""
python main.py 2               # 全量历史切分（70/15/15）
python main.py 2 --rolling     # 滚动窗口（最近2年训练，推荐）
python main.py 2 --walk-forward  # WF-CV诊断 + 滚动训练
python main.py 2 --rolling --final  # 评估后用全量数据重训生产模型
""", s)

    story += [Paragraph("4.1 时序切分（滚动窗口）", s["h2"])]
    story.append(Paragraph(
        "滚动窗口模式使用最近2年（504交易日）训练，避免远期数据噪音影响近期预测。", s["body"]))
    split = [
        ["分割", "日期范围", "样本量", "用途"],
        ["训练集", "2022-08-29 ~ 2024-09-25", "约 262万行", "LightGBM + Ridge 拟合"],
        ["验证集", "2024-09-26 ~ 2025-07-17", "约 104万行", "Early Stopping + 集成权重优化"],
        ["测试集", "2025-07-18 ~ 2026-05-11", "约 100万行", "泛化能力评估（不参与训练）"],
    ]
    story.append(table(split, [0.16, 0.28, 0.22, 0.34]))

    story += [Paragraph("4.2 模型架构", s["h2"])]
    story.append(Paragraph(
        "双模型集成：LightGBM 提供分类概率，Ridge 提供连续预测，加权后作为最终信号。", s["body"]))
    model_arch = [
        ["组件", "输入", "输出", "说明"],
        ["LightGBM", "技术/基本面/信号因子", "P(跌)/P(震)/P(涨)", "三分类，Early Stopping（验证集logloss）"],
        ["Ridge回归", "相同因子集", "连续收益预测", "辅助信号，压制lgbm极端预测"],
        ["集成", "lgbm_prob_up + ridge_pred", "最终信号值", "signal = w_lgbm×P(涨) + w_ridge×预测"],
    ]
    story.append(table(model_arch, [0.14, 0.22, 0.22, 0.42]))

    story += [Paragraph("4.3 IC 验收结果（M3）", s["h2"])]
    ic_results = [
        ["分割", "IC", "准确率", "加权F1"],
        ["训练集", "0.1240", "39.1%", "0.362"],
        ["验证集", "0.0807", "37.8%", "0.349"],
        ["测试集", "0.0197", "38.5%", "0.360"],
    ]
    story.append(table(ic_results, [0.25, 0.25, 0.25, 0.25]))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(
        "✓ 测试集 IC 0.0197 = 基准 0.0197（M3框架无退步）。"
        "北向因子已正常填充（66.7%覆盖）；龙虎榜/研报/EPS 因子待采集数据后预期进一步提升 IC。",
        s["note"]))

    story.append(PageBreak())

    # ════════════════════════════════════════════
    # 5. Phase 3 回测
    # ════════════════════════════════════════════
    story += [Paragraph("5. Phase 3：回测", s["h1"]), hr()]
    story += code_block("""
python main.py 3                          # 标准回测（Top-50，5日调仓）
python main.py 3 --top-k 30 --rebalance 10  # 调整持仓数和换手频率
python main.py 3 --replace-only          # 散户模式（减少主动换手）
""", s)

    story += [Paragraph("5.1 回测参数", s["h2"])]
    params = [
        ["参数", "默认值", "说明"],
        ["持仓数 --top-k", "50", "每期持仓最多N只（候选池1.5×N）"],
        ["调仓间隔 --rebalance", "5 天", "每5个交易日调仓一次"],
        ["单股权重 --max-weight", "5%", "单只股票最大仓位"],
        ["板块权重 --max-sector-weight", "40%", "单板块最大仓位（分散风险）"],
        ["换手约束 --max-turnover", "50%", "单次单向换手率上限"],
        ["手续费", "万3（双向）", "买卖各万3"],
        ["印花税", "千1（卖方）", "仅卖出收取"],
        ["滑点", "0.2%", "双向合计"],
        ["基准", "沪深300", "等权重对比"],
    ]
    story.append(table(params, [0.30, 0.20, 0.50]))

    story += [Paragraph("5.2 股票过滤规则", s["h2"])]
    filters = [
        ["规则", "说明"],
        ["排除ST/*ST", "使用akshare实时名称列表，每7天刷新缓存"],
        ["排除科创板", "688xxx前缀，需单独开通权限"],
        ["排除新股", "上市不足250交易日（约1年）"],
        ["信号过滤", "--min-signal 参数，低于阈值不纳入选股池（默认0.0不过滤）"],
    ]
    story.append(table(filters, [0.25, 0.75]))

    story.append(PageBreak())

    # ════════════════════════════════════════════
    # 6. 每日增量更新
    # ════════════════════════════════════════════
    story += [Paragraph("6. 每日增量更新", s["h1"]), hr()]
    story.append(Paragraph(
        "日常使用无需重跑全量 Phase 1，通过 watermark.json 水位机制只处理新增交易日数据。", s["body"]))
    story += code_block("""
# 每日收盘后运行（2-3 分钟）
python main.py update

# 额外更新腾讯财经 PE/PB 快照
python main.py update --with-tencent

# 更新后自动触发 Phase 2 重训
python main.py update --retrain
""", s)

    story += [Paragraph("增量更新步骤", s["h3"])]
    update_steps = [
        ["步骤", "操作", "水位更新"],
        ["① K线增量", "mootdx TCP 拉取新K线，追加至 data/raw/kline/", "watermark.kline"],
        ["② 北向快照", "NorthboundCollector 追加今日北向数据", "watermark.northbound"],
        ["③ 腾讯快照", "TencentCollector（--with-tencent 时）", "watermark.tencent"],
        ["④ 增量特征", "assemble_incremental()，只计算 since 水位后的新行", "watermark.features"],
        ["⑤ 重训（可选）", "--retrain 触发 run_training_rolling()", "—"],
    ]
    story.append(table(update_steps, [0.10, 0.55, 0.35]))

    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        "月初全量刷新（月度通达信数据包）：", s["h3"]))
    story += code_block("""
python main.py sync --zip hsjday-YYYY-MM-DD.zip   # 解压并确认
python main.py 1                                   # 全量重建特征
python main.py 2 --rolling                         # 重训模型
""", s)

    story.append(PageBreak())

    # ════════════════════════════════════════════
    # 7. 选股扫描
    # ════════════════════════════════════════════
    story += [Paragraph("7. 选股扫描（scan）", s["h1"]), hr()]
    story.append(Paragraph(
        "快捷指令，读取最新截面推断特征并输出当日 Top-N 候选股排名，供盘后决策参考。", s["body"]))
    story += code_block("""
python main.py scan            # 默认 Top-20，缓冲区1.5×
python main.py scan --top-k 30 # 调整候选数
""", s)

    story += [Paragraph("输出说明", s["h3"])]
    scan_cols = [
        ["字段", "说明"],
        ["rank", "全市场信号强度排名"],
        ["code", "股票代码"],
        ["segment", "板块（主板/中小板/创业板等）"],
        ["close", "最新收盘价"],
        ["signal", "集成信号值（w_lgbm×P(涨) + w_ridge×预测）"],
        ["signal_pct", "全市场分位（1.0=最强）"],
    ]
    story.append(table(scan_cols, [0.18, 0.82]))
    story.append(Paragraph(
        "排名前 Top-K 标注 ◀ 为建仓候选；继续持有条件：仍在前 1.5×Top-K 名内；"
        "跌出 1.5×Top-K → 纳入卖出候选（散户模式 --replace-only 进一步降低换手）。", s["note"]))

    story.append(PageBreak())

    # ════════════════════════════════════════════
    # 8. 数据采集器说明
    # ════════════════════════════════════════════
    story += [Paragraph("8. 数据采集器（Collectors）", s["h1"]), hr()]
    collectors = [
        ["采集器", "数据源", "输出", "接口"],
        ["TDXCollector", "通达信本地.day文件", "data/raw/kline/{code}.parquet", "fetch_all() / fetch_incremental_mootdx()"],
        ["NorthboundCollector", "同花顺THS", "data/raw/northbound.parquet", "fetch_all([])"],
        ["FundamentalsCollector", "akshare东财", "data/fundamentals/{code}.parquet", "fetch_all(codes)"],
        ["FundFlowCollector", "akshare东财", "data/fund_flow/{code}.parquet", "fetch_all(codes)"],
        ["TencentCollector", "腾讯财经", "data/raw/tencent/{code}.parquet", "fetch_all(codes)"],
        ["ReportCollector\n(mode=report)", "akshare东财研报", "data/raw/reports/{code}.parquet", "fetch_all(codes, mode='report')"],
        ["ReportCollector\n(mode=eps)", "同花顺THS EPS", "data/raw/eps/{code}.parquet", "fetch_all(codes, mode='eps')"],
        ["SignalCollector", "akshare东财龙虎榜", "data/raw/lhb/YYYY-MM-DD.parquet", "fetch_all(codes, date=d)"],
    ]
    story.append(table(collectors, [0.20, 0.18, 0.30, 0.32]))

    story += [Paragraph("BaseCollector 通用机制", s["h3"])]
    story.append(Paragraph(
        "所有采集器继承 BaseCollector，具备以下共同能力：", s["body"]))
    for item in [
        "增量跳过（incremental=True）：检查本地缓存，有效则跳过，返回 stats.cached",
        "断路器（max_errors）：连续失败超阈值后自动终止，避免封禁",
        "进度追踪：tqdm 进度条 + CollectStats(ok/fail/cached) 返回",
        "延迟控制：请求间默认延迟，可通过 delay 参数调整",
    ]:
        story.append(Paragraph(f"• {item}", s["bullet"]))

    story.append(PageBreak())

    # ════════════════════════════════════════════
    # 9. 常见问题
    # ════════════════════════════════════════════
    story += [Paragraph("9. 常见问题与处理", s["h1"]), hr()]
    faq = [
        ["问题", "原因", "处理方法"],
        ["market_features.parquet 行数异常少",
         "调试模式 sample_size 参数遗留，\n或 Phase 1 中途中断",
         "python main.py 1 全量重建"],
        ["测试集 IC 异常低（接近0）",
         "关键因子全为 NaN（如北向因子）",
         "检查 assembler 是否正确调用\nadd_signal_features；查看日志"],
        ["north_net_5d 全为 NaN",
         "assembler 重复 merge 导致列名被\npandas 加 _x/_y 后缀",
         "已修复（ea1d1a8）：删除了\n重复的 _merge_northbound 调用"],
        ["回测末期资产归零",
         "market_features.parquet 因 dropna(label)\n导致最后5个交易日数据缺失",
         "保证通达信数据截止日期在\n标签计算期之后（+5交易日）"],
        ["akshare 接口限流/超时",
         "请求频率过高",
         "增大 --delay 参数；断路器\n触发后重跑（自动跳过已缓存）"],
        ["新股/ST误入选股池",
         "ST缓存过期或代码识别失败",
         "ST_CACHE_DAYS=7 自动刷新；\n手动执行 get_st_codes(refresh=True)"],
    ]
    story.append(table(faq, [0.28, 0.32, 0.40]))

    story.append(PageBreak())

    # ════════════════════════════════════════════
    # 10. 快速参考
    # ════════════════════════════════════════════
    story += [Paragraph("10. 命令快速参考", s["h1"]), hr()]
    cmds = [
        ["命令", "场景"],
        ["python main.py sync --zip <压缩包>", "月度：导入通达信数据包"],
        ["python main.py collect", "月度：转换K线 + 北向快照"],
        ["python main.py fetch-fund", "季度：刷新PE/PB/市值基本面"],
        ["python main.py fetch-flow", "季度：刷新主力资金流向"],
        ["python main.py 1", "月度：全量重建特征（~20分钟）"],
        ["python main.py 2 --rolling", "月度：滚动窗口重训模型"],
        ["python main.py 3", "按需：回测评估"],
        ["python main.py update", "每日：增量更新K线+特征"],
        ["python main.py update --retrain", "每日：更新+重训"],
        ["python main.py scan", "每日收盘：输出选股信号"],
        ["python main.py 1 --sample 50", "调试：仅处理50只股票"],
        ["python main.py 2 --walk-forward", "诊断：WF-CV验证模型稳定性"],
    ]
    story.append(table(cmds, [0.55, 0.45]))

    story += [Spacer(1, 1 * cm), hr(C_GRAY, 0.5)]
    story.append(Paragraph(
        f"文档生成时间：{date.today().strftime('%Y-%m-%d')}  |  "
        "项目：quant_trading  |  模型版本：M3（北向迁移 + 龙虎榜/研报/EPS 框架）",
        ParagraphStyle("footer", fontName=FONT, fontSize=8,
                       textColor=C_GRAY, alignment=1)))

    doc.build(story)
    print(f"✅ PDF 已生成：{output_path}")


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    out = root / "docs" / "quant_trading_workflow.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    build(out)
