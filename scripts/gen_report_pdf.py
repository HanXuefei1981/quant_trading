# -*- coding: utf-8 -*-
"""A股量化交易模型设计方案 PDF 生成脚本"""
import sys
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 字体注册
FONT_DIR = Path("/mnt/c/Windows/Fonts")
pdfmetrics.registerFont(TTFont("SimHei", str(FONT_DIR / "simhei.ttf")))
pdfmetrics.registerFont(TTFont("SimKai", str(FONT_DIR / "simkai.ttf")))

# ── 颜色
C_DARK   = colors.HexColor("#0d3460")
C_BLUE   = colors.HexColor("#1a5276")
C_LIGHT  = colors.HexColor("#2874a6")
C_GRAY   = colors.HexColor("#888888")
C_RED    = colors.HexColor("#922b21")
C_TEXT   = colors.HexColor("#1a1a1a")
HDR_BG   = colors.HexColor("#1a5276")
ROW_ALT  = colors.HexColor("#eaf4fb")
ROW_WARN = colors.HexColor("#fdecea")


def S(name, **kw):
    defaults = dict(fontName="SimHei", fontSize=10, leading=16,
                    textColor=C_TEXT, spaceAfter=4)
    defaults.update(kw)
    return ParagraphStyle(name, **defaults)


st_cover_title = S("cover_title", fontSize=26, leading=34, spaceAfter=10,
                   textColor=C_DARK, alignment=1)
st_cover_sub   = S("cover_sub",   fontSize=14, leading=22, spaceAfter=6,
                   textColor=C_BLUE, alignment=1)
st_cover_meta  = S("cover_meta",  fontSize=10, leading=16, spaceAfter=4,
                   textColor=C_GRAY, alignment=1)
st_h1   = S("h1",  fontSize=16, leading=24, spaceBefore=18, spaceAfter=8,
            textColor=C_DARK)
st_h2   = S("h2",  fontSize=13, leading=20, spaceBefore=12, spaceAfter=6,
            textColor=C_BLUE)
st_h3   = S("h3",  fontSize=11, leading=18, spaceBefore=8,  spaceAfter=4,
            textColor=C_LIGHT)
st_body = S("body", fontSize=10, leading=17, spaceAfter=4)
st_code = S("code", fontName="SimHei", fontSize=8.5, leading=13,
            backColor=colors.HexColor("#f4f4f4"), spaceAfter=3,
            leftIndent=12, rightIndent=12, borderPad=4)
st_note = S("note", fontSize=9, leading=14, textColor=colors.HexColor("#555555"),
            leftIndent=12, fontName="SimKai")
st_warn = S("warn", fontSize=9.5, leading=15, textColor=C_RED,
            leftIndent=8, spaceBefore=4, spaceAfter=6)


def H1(t): return Paragraph(t, st_h1)
def H2(t): return Paragraph(t, st_h2)
def H3(t): return Paragraph(t, st_h3)
def P(t):  return Paragraph(t, st_body)
def Note(t): return Paragraph("[注] " + t, st_note)
def Warn(t): return Paragraph("[!] " + t, st_warn)


def Code(t):
    return Paragraph(
        t.replace("\n", "<br/>").replace(" ", "&nbsp;"),
        st_code
    )


def SP(h=6): return Spacer(1, h)
def HR(): return HRFlowable(width="100%", thickness=0.5,
                             color=colors.HexColor("#cccccc"), spaceAfter=4)


def tbl(data, col_widths, header=True, warn_rows=None):
    style = [
        ("FONTNAME",     (0, 0), (-1, -1), "SimHei"),
        ("FONTSIZE",     (0, 0), (-1, -1), 9),
        ("LEADING",      (0, 0), (-1, -1), 14),
        ("GRID",         (0, 0), (-1, -1), 0.3, colors.HexColor("#aaaaaa")),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1),
         [colors.white, ROW_ALT]),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), HDR_BG),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTSIZE",   (0, 0), (-1, 0), 9.5),
        ]
    if warn_rows:
        for r in warn_rows:
            style.append(("BACKGROUND", (0, r), (-1, r), ROW_WARN))
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle(style))
    return t


# ────────────────────────── 正文 ──────────────────────────────────────────────
def build_story():
    W = A4[0] - 4 * cm  # 页面可用宽度

    story = []

    # ===== 封面 =====
    story += [
        SP(80),
        Paragraph("A股量化交易模型", st_cover_title),
        Paragraph("设计方案与验证分析报告", st_cover_sub),
        SP(20), HR(), SP(10),
        Paragraph("版本 v1.0 &nbsp;|&nbsp; 2026-05-08", st_cover_meta),
        Paragraph("数据源：通达信本地 .day 文件 &nbsp;|&nbsp; 模型：LightGBM 三分类", st_cover_meta),
        Paragraph("回测区间：2024-09-19 至 2026-04-27（386 个交易日）", st_cover_meta),
        PageBreak(),
    ]

    # ===== 一、系统概述 =====
    story += [H1("一、系统概述"), HR()]
    story += [P("本系统为基于机器学习的 A 股组合投资策略，"
                "采用【数据-特征-模型-回测】四阶段流水线架构，"
                "全程使用通达信本地行情数据，不依赖实时行情 API。")]
    story += [
        SP(4),
        tbl([
            ["阶段",    "模块",              "说明"],
            ["Phase 1", "数据管道 + 特征工程", "从通达信 .day 文件提取 OHLCV，计算 30 个技术因子，生成训练数据集"],
            ["Phase 2", "模型训练",           "LightGBM 三分类（涨/震荡/跌），严格时序切分，防止未来信息泄漏"],
            ["Phase 3", "回测引擎",           "基于模型信号的 Top-K 等权多股票组合，含手续费/印花税/滑点"],
        ], [3 * cm, 5.5 * cm, W - 8.5 * cm]),
        SP(8),
    ]

    story += [
        H2("1.1 关键路径"),
        Code(
            "通达信 vipdoc/ -> pipeline.py -> market_features.parquet\n"
            "                                      |\n"
            "                                 trainer.py -> lgbm_model.joblib\n"
            "                                      |\n"
            "                                 engine.py  -> equity_curve.csv"
        ),
        SP(4),
    ]

    story += [
        H2("1.2 全局参数"),
        tbl([
            ["参数",             "值",           "说明"],
            ["START_DATE",       "20210101",     "数据起始日期"],
            ["END_DATE",         "20260507",     "数据截止日期"],
            ["FORWARD_DAYS",     "5",            "预测未来 N 日收益"],
            ["UP_THRESHOLD",     "+3%",          "涨幅阈值，超过则 label=+1"],
            ["DOWN_THRESHOLD",   "-3%",          "跌幅阈值，低于则 label=-1"],
            ["MIN_TRADE_DAYS",   "250",          "上市不足 250 日的股票过滤"],
            ["EXCLUDE_ST",       "True",         "排除 ST/*ST 股票"],
            ["INITIAL_CAPITAL",  "100 万元",     "回测初始资金"],
            ["COMMISSION_RATE",  "万3（双向）",  "手续费率"],
            ["STAMP_DUTY",       "千1（卖方）",  "印花税"],
            ["SLIPPAGE",         "0.2%",         "滑点假设"],
            ["RANDOM_SEED",      "42",           "随机种子"],
            ["TRAIN_RATIO",      "70%",          "训练集比例"],
            ["VAL_RATIO",        "15%",          "验证集比例"],
            ["TEST_RATIO",       "15%",          "测试集比例"],
        ], [4.5 * cm, 3.5 * cm, W - 8 * cm]),
        SP(4),
    ]

    # ===== 二、数据管道 =====
    story += [PageBreak(), H1("二、数据管道（Phase 1）"), HR()]
    story += [P("原始行情数据来源于通达信本地磁盘（Windows 侧通过 WSL /mnt/e 挂载），"
                "读取 .day 格式二进制文件，解析为 OHLCV 日线数据。")]
    story += [
        SP(4),
        tbl([
            ["项目",     "内容"],
            ["数据源路径",  "/mnt/e/new_tdx/vipdoc/"],
            ["股票宇宙",   "全 A 股 5,767 只（原始），过滤后 5,523 只"],
            ["过滤规则",   "排除上市不足 250 日、ST/*ST、非个股代码（ETF/指数）"],
            ["复权方式",   "通达信日线已含前复权价，无需额外处理"],
            ["数据范围",   "2021-01-05 至 2026-04-27（含 label 的末端）"],
            ["输出文件",   "data/processed/market_features.parquet"],
            ["数据规模",   "6,440,080 行 x 51 列"],
            ["关键约束",   "末端日期 = END_DATE - 5 个交易日（label 需要未来收益率）"],
        ], [4 * cm, W - 4 * cm]),
        SP(8),
    ]

    story += [
        H2("2.1 数据质量原则"),
        Note("外部数据（通达信、akshare）的质量问题不修改程序逻辑绕过，"
             "而是稽核数据完整性并告知用户。"),
        Note("关键列（date, open, high, low, close, volume）经 dropna 过滤，已确认无 NaN。"),
        SP(4),
    ]

    # ===== 三、因子工程 =====
    story += [PageBreak(), H1("三、因子工程（Feature Engineering）"), HR()]
    story += [P("共计 <b>30 个输入特征</b>，分为趋势、震荡、波动、量价四大类。"
                "所有特征均为<b>归一化相对值</b>（价格比率或百分比形式），"
                "避免不同股票价格量级差异对模型造成干扰。")]
    story += [SP(6)]

    story += [
        H2("3.1 趋势类因子（8个）"),
        tbl([
            ["因子名",        "计算公式",                       "经济含义"],
            ["ma5_ratio",    "close / MA(5) - 1",              "收盘价相对5日均线偏离度"],
            ["ma10_ratio",   "close / MA(10) - 1",             "收盘价相对10日均线偏离度"],
            ["ma20_ratio",   "close / MA(20) - 1",             "收盘价相对20日均线偏离度"],
            ["ma60_ratio",   "close / MA(60) - 1",             "收盘价相对60日均线偏离度"],
            ["macd_dif",     "EMA(12) - EMA(26)",              "MACD 快慢线差值（DIF）"],
            ["macd_dea",     "EMA(DIF, 9)",                    "MACD 信号线（DEA）"],
            ["macd_hist",    "(DIF - DEA) x 2",                "MACD 柱状图"],
            ["macd_cross",   "sign(DIF-DEA).diff()",           "金叉/死叉：+2金叉，-2死叉"],
        ], [3.5 * cm, 5 * cm, W - 8.5 * cm]),
        SP(6),
    ]

    story += [
        H2("3.2 震荡类因子（6个）"),
        tbl([
            ["因子名",           "计算公式",                            "经济含义"],
            ["rsi",             "100 - 100/(1+RS),  RS=EMA增/EMA减",  "RSI 相对强弱指数（14日）"],
            ["rsi_oversold",    "rsi < 30 -> 1",                      "超卖信号（0/1 布尔量）"],
            ["rsi_overbought",  "rsi > 70 -> 1",                      "超买信号（0/1 布尔量）"],
            ["kdj_k",           "RSV 的 EMA(1/3)",                    "KDJ K值（9日周期）"],
            ["kdj_d",           "K 的 EMA(1/3)",                      "KDJ D值"],
            ["kdj_j",           "3K - 2D",                            "KDJ J值（超买超卖敏感）"],
        ], [3.5 * cm, 5.5 * cm, W - 9 * cm]),
        SP(6),
    ]

    story += [
        H2("3.3 波动类因子（7个）— G1实验屏蔽集"),
        tbl([
            ["因子名",          "计算公式",                    "经济含义",                "G1状态"],
            ["boll_width",     "(上轨-下轨) / 中轨",          "布林带宽度（20日），反映波动扩张/收缩", "保留"],
            ["boll_pct",       "(close-下轨) / (上轨-下轨)", "价格在布林带中的相对位置（0~1）",       "保留"],
            ["atr",            "EMA(TR, 14)",                 "平均真实波幅（绝对值）",     "屏蔽"],
            ["atr_ratio",      "ATR / close",                 "标准化波幅（相对价格）",     "屏蔽"],
            ["volatility5",    "std(ret1, 5)",                "5日收益率标准差",            "屏蔽"],
            ["volatility20",   "std(ret1, 20)",               "20日收益率标准差",           "屏蔽"],
            ["high_low_ratio", "(high - low) / close",        "日内振幅（相对价格）",       "屏蔽"],
        ], [3.2 * cm, 4.5 * cm, W - 10.5 * cm, 1.8 * cm],
        warn_rows=[3, 4, 5, 6, 7]),
        Note("G1实验结论：屏蔽5个波动因子后，测试集IC微升0.0037（0.0503->0.0540），"
             "结论是波动因子对模型无显著负面影响，根本问题另在别处。"),
        SP(6),
    ]

    story += [
        H2("3.4 量价类因子（9个）"),
        tbl([
            ["因子名",              "计算公式",                   "经济含义"],
            ["vol_ratio",          "volume / MA(volume, 5)",     "成交量与5日均量之比（放量/缩量信号）"],
            ["vol_trend",          "MA(vol,5) / MA(vol,20)",     "量能趋势（短均量/长均量）"],
            ["ret1",               "close / close.shift(1) - 1", "日收益率"],
            ["ret5",               "close / close.shift(5) - 1", "5日动量"],
            ["ret10",              "close / close.shift(10) - 1","10日动量"],
            ["ret20",              "close / close.shift(20) - 1","20日动量"],
            ["open_close_ratio",   "(close - open) / open",      "实体涨跌幅（开盘到收盘）"],
            ["upper_shadow",       "(high - max(o,c)) / close",  "上影线比率"],
            ["lower_shadow",       "(min(o,c) - low) / close",   "下影线比率"],
        ], [3.8 * cm, 5 * cm, W - 8.8 * cm]),
        SP(6),
    ]

    story += [
        H2("3.5 特征排除列表"),
        P("以下列存在于 DataFrame 但<b>不作为模型输入</b>，由 get_feature_columns() 排除："),
        Code(
            "排除列：date, open, high, low, close, volume, amount, turnover,\n"
            "        future_ret, label,\n"
            "        boll_upper, boll_mid, boll_lower,\n"
            "        ma5, ma10, ma20, ma60, ema12, ema26,\n"
            "        vol_ma5, vol_ma20"
        ),
        Note("排除原因：绝对价格/均线值含量级信息，跨股票不可比；"
             "future_ret 和 label 为目标变量，排除防止数据泄漏。"),
        SP(4),
    ]

    # ===== 四、标签设计 =====
    story += [PageBreak(), H1("四、标签设计"), HR()]

    story += [
        H2("4.1 基准标签（原始模型 & G1）"),
        P("基于<b>绝对收益率</b>划分三分类，预测未来5个交易日涨跌幅："),
        Code(
            "future_ret = close.shift(-5) / close - 1\n"
            "\n"
            "label = +1 (涨)   if future_ret >= +3%\n"
            "label = -1 (跌)   if future_ret <= -3%\n"
            "label =  0 (震荡) otherwise"
        ),
        tbl([
            ["标签", "含义", "原始数据集占比"],
            ["+1",  "看涨", "26.0%"],
            ["0",   "震荡", "46.5%"],
            ["-1",  "看跌", "27.5%"],
        ], [2 * cm, 3 * cm, 4 * cm]),
        Note("三分类不均衡（震荡类占比最高），LightGBM 使用 is_unbalance=True 自动处理样本权重。"),
        SP(8),
    ]

    story += [
        H2("4.2 G2 实验：截面排名标签（去 beta）"),
        P("将绝对收益率改为<b>截面排名分位数</b>，每日按全市场 future_ret 排名，"
          "消除市场整体 beta 的影响："),
        Code(
            "future_ret_rank = groupby(date)[future_ret].rank(pct=True)\n"
            "\n"
            "label = +1 (涨)   if rank >= 70%\n"
            "label = -1 (跌)   if rank <= 30%\n"
            "label =  0 (震荡) otherwise"
        ),
        tbl([
            ["标签", "比例", "说明"],
            ["+1",  "30%", "每日截面排名前30%（相对市场的强势股）"],
            ["0",   "40%", "截面中间40%"],
            ["-1",  "30%", "每日截面排名后30%（相对市场的弱势股）"],
        ], [2 * cm, 2.5 * cm, W - 4.5 * cm]),
        Warn("G2 实验结论：截面标签早停仅36轮（模型严重欠拟合），"
             "测试集 IC 降至 0.025，效果显著差于原始绝对标签。"
             "原因是丢失了绝对收益信息，模型无法学到有效规律。"),
        SP(4),
    ]

    # ===== 五、模型训练 =====
    story += [PageBreak(), H1("五、模型训练（Phase 2）"), HR()]

    story += [
        H2("5.1 时序切分（严格防数据泄漏）"),
        P("按日期顺序切分，<b>不做随机打乱</b>，确保训练集中无任何未来信息："),
        tbl([
            ["集合",   "比例", "日期范围",                    "样本行数",    "交易日数"],
            ["训练集", "70%", "2021-01-05 至 2024-09-18",    "4,409,984",  "899"],
            ["验证集", "15%", "2024-09-19 至 2025-07-09",    "1,031,065",  "193"],
            ["测试集", "15%", "2025-07-10 至 2026-04-27",    "999,031",    "193"],
        ], [2.5 * cm, 1.8 * cm, 5.5 * cm, 3 * cm, 2.5 * cm]),
        Note("切分单位为交易日数（不是行数），确保每日所有股票均属于同一集合，"
             "防止跨日信息泄漏。"),
        SP(6),
    ]

    story += [
        H2("5.2 LightGBM 超参数详情"),
        tbl([
            ["参数",               "值",            "说明"],
            ["objective",          "multiclass",    "多分类目标函数"],
            ["num_class",          "3",             "涨 / 震荡 / 跌 三类"],
            ["metric",             "multi_logloss", "验证集监控指标（交叉熵损失）"],
            ["learning_rate",      "0.05",          "步长，较小值配合early stopping防过拟合"],
            ["num_leaves",         "63",            "每棵树最大叶节点数（复杂度控制）"],
            ["max_depth",          "-1",            "不限制深度（由num_leaves隐式控制）"],
            ["min_child_samples",  "50",            "叶节点最少样本数（防过拟合）"],
            ["feature_fraction",   "0.8",           "每次迭代随机使用80%特征"],
            ["bagging_fraction",   "0.8",           "每次迭代随机使用80%数据行"],
            ["bagging_freq",       "5",             "每5轮执行一次bagging"],
            ["is_unbalance",       "True",          "自动处理三类样本不均衡（调整类权重）"],
            ["num_boost_round",    "2000",          "最大迭代轮数上限"],
            ["early_stopping",     "50",            "验证集连续50轮无改善则停止训练"],
            ["n_jobs",             "-1",            "使用全部CPU核心并行训练"],
            ["seed",               "42",            "随机种子（保证结果可复现）"],
        ], [4 * cm, 3.5 * cm, W - 7.5 * cm]),
        SP(6),
    ]

    story += [
        H2("5.3 标签映射"),
        Code(
            "训练输入：{-1 -> 0,  0 -> 1,  +1 -> 2}  (LightGBM要求标签从0开始)\n"
            "预测输出：{0  -> -1, 1 -> 0,  2  -> +1} (还原为业务含义)"
        ),
        SP(4),
    ]

    story += [
        H2("5.4 实际早停轮数"),
        tbl([
            ["模型",          "早停轮数", "说明"],
            ["原模型",        "未记录",  "正常收敛"],
            ["G1（无波动率）", "74",      "去掉波动率因子后收敛轮数略减"],
            ["G2（截面标签）", "36",      "极早停止，说明截面标签信息量不足，模型欠拟合"],
        ], [4 * cm, 3 * cm, W - 7 * cm]),
        SP(4),
    ]

    # ===== 六、模型评估 =====
    story += [PageBreak(), H1("六、模型评估指标体系"), HR()]

    story += [
        H2("6.1 指标定义"),
        tbl([
            ["指标",            "计算方式",                                 "解读标准"],
            ["Accuracy",       "预测正确样本数 / 总样本数",                 "> 0.5 为优，基准为最大类比例（约46%震荡）"],
            ["F1 (weighted)",  "各类 F1 按样本量加权平均",                  "综合精确率与召回率，比 Accuracy 更可靠"],
            ["Direction Acc",  "仅在预测涨(+1)或跌(-1)的样本上计算准确率", "过滤震荡预测噪音，代表交易信号质量，> 0.5为正预测值"],
            ["IC",             "Pearson(P涨 - P跌,  future_ret)",          "> 0.05 为有效因子，> 0.10 为优质因子"],
        ], [3.5 * cm, 5.5 * cm, W - 9 * cm]),
        SP(6),
    ]

    story += [
        H2("6.2 IC（信息系数）计算细节"),
        Code(
            "# 模型输出 proba shape = (n_samples, 3)\n"
            "#   proba[:,0] = P(跌)，proba[:,1] = P(震荡)，proba[:,2] = P(涨)\n"
            "\n"
            "score = proba[:,2] - proba[:,0]   # 多空得分（连续值）\n"
            "IC    = pearsonr(score, future_ret)[0]"
        ),
        Note("IC 使用 Pearson 相关系数衡量模型排序能力，"
             "分子是概率差（连续值），比直接用预测类别更稳健。"),
        SP(6),
    ]

    story += [
        H2("6.3 三模型评估结果完整对比"),
        tbl([
            ["集合",   "指标",       "原模型",  "G1（无波动率）", "G2（截面标签）"],
            ["训练集", "Accuracy",  "0.5134",  "0.5098",        "0.4586"],
            ["",       "F1",        "0.4572",  "0.4495",        "0.3825"],
            ["",       "Dir Acc",   "0.4692",  "0.4729",        "0.4364"],
            ["",       "IC",        "0.2253",  "0.2196",        "0.1288"],
            ["验证集", "Accuracy",  "0.4726",  "0.4693",        "0.4434"],
            ["",       "F1",        "0.4056",  "0.4010",        "0.3743"],
            ["",       "Dir Acc",   "0.3881",  "0.3845",        "0.3936"],
            ["",       "IC",        "0.0902",  "0.0893",        "0.0573"],
            ["测试集", "Accuracy",  "0.5138",  "0.5125",        "0.4564"],
            ["",       "F1",        "0.4284",  "0.4268",        "0.3742"],
            ["",       "Dir Acc",   "0.3920",  "0.3932",        "0.4082"],
            ["",       "IC",        "0.0503",  "0.0540",        "0.0249"],
        ], [2.5 * cm, 3 * cm, 3 * cm, 3.5 * cm, 3.5 * cm]),
        SP(6),
    ]

    story += [
        H2("6.4 IC 泛化衰减分析（核心问题）"),
        tbl([
            ["模型",          "训练IC", "验证IC", "测试IC", "验证->测试衰减", "早停"],
            ["原模型",        "0.2253", "0.0902", "0.0503", "44%",          "—"],
            ["G1（无波动率）", "0.2196", "0.0893", "0.0540", "40%",          "74轮"],
            ["G2（截面标签）", "0.1288", "0.0573", "0.0249", "57%",          "36轮"],
        ], [4 * cm, 2 * cm, 2 * cm, 2 * cm, 3.5 * cm, 2 * cm],
        warn_rows=[1, 2, 3]),
        Warn("三个模型训练IC均超过0.12，但测试IC均低于0.055，"
             "验证到测试衰减幅度40~57%。"
             "表明模型存在时间分布漂移（domain shift），"
             "2025年后的市场规律与训练期存在显著差异。"),
        SP(4),
    ]

    # ===== 七、回测引擎 =====
    story += [PageBreak(), H1("七、回测引擎（Phase 3）"), HR()]

    story += [
        H2("7.1 信号生成"),
        P("模型对每只股票每个交易日输出三类概率，信号值定义为："),
        Code(
            "signal = P(涨) - P(跌)   # 多空净概率差\n"
            "取值范围：(-1, +1)\n"
            "  signal > 0：模型倾向看多\n"
            "  signal < 0：模型倾向看空\n"
            "  |signal|越大：模型置信度越高"
        ),
        SP(4),
    ]

    story += [
        H2("7.2 股票过滤规则"),
        P("生成信号时过滤非个股代码，仅保留以下前缀："),
        Code(
            "保留：6xxxxx（沪市主板/科创板）\n"
            "      000xxx, 001xxx, 002xxx, 003xxx（深市主板）\n"
            "      300xxx, 301xxx（创业板）\n"
            "      8xxxxx（北交所）\n"
            "排除：指数、ETF、债券等非个股代码"
        ),
        SP(4),
    ]

    story += [
        H2("7.3 调仓策略"),
        tbl([
            ["参数",           "默认值", "说明"],
            ["top_k",          "50",    "每期持有信号最强的前K只股票"],
            ["rebalance_every", "5日",  "每5个交易日调仓一次（约每周）"],
            ["min_signal",     "0.0",   "信号阈值，signal <= 0 的股票不入组合"],
            ["续留规则",       "—",     "已持有且仍在Top-K中的股票保持不动（减少换手成本）"],
        ], [4 * cm, 2.5 * cm, W - 6.5 * cm]),
        SP(4),
    ]

    story += [
        H2("7.4 资金分配规则"),
        Code(
            "调仓时：\n"
            "  1. 卖出不在新 Top-K 中的持仓（扣手续费+印花税+滑点）\n"
            "  2. 将回收现金 + 闲置现金 等额分配给新增股票\n"
            "  3. per_stock = (可用现金 - 预估手续费) / 新增股票数\n"
            "\n"
            "日内净值更新：\n"
            "  持仓金额 x= (next_close / today_close)   # 按涨跌幅更新市值\n"
            "  停牌股票保持持仓金额不变"
        ),
        SP(4),
    ]

    story += [
        H2("7.5 交易成本模型"),
        tbl([
            ["费用",       "费率",   "方向",       "备注"],
            ["手续费",     "万3",    "买入 + 卖出", "COMMISSION_RATE = 0.0003"],
            ["印花税",     "千1",    "仅卖出方",   "STAMP_DUTY = 0.001"],
            ["滑点",       "0.2%",  "买入 + 卖出", "SLIPPAGE = 0.002"],
            ["买入总成本", "0.5%",  "买入",        "COMMISSION + SLIPPAGE"],
            ["卖出总成本", "0.6%",  "卖出",        "COMMISSION + STAMP + SLIPPAGE"],
        ], [3 * cm, 2 * cm, 3 * cm, W - 8 * cm]),
        SP(4),
    ]

    story += [
        H2("7.6 基准定义"),
        P("<b>等权全市场基准</b>：每日等权持有全部有效股票，"
          "计算全市场平均日收益率并累积净值："),
        Code(
            "daily_ret = signal_df.groupby('date')['ret1'].mean()\n"
            "benchmark = (1 + daily_ret).cumprod() * INITIAL_CAPITAL"
        ),
        Warn("注意：等权全市场基准在 2024Q4 行情中收益异常高（总收益107%），"
             "原因是等权放大了小市值效应，"
             "该基准不可实际复制，与策略对比存在偏差。"
             "建议改用沪深300或中证500作为基准。"),
        SP(4),
    ]

    # ===== 八、回测绩效 =====
    story += [PageBreak(), H1("八、回测绩效结果"), HR()]

    story += [
        H2("8.1 绩效指标公式"),
        tbl([
            ["指标",     "公式",                                     "参数"],
            ["总收益率", "equity[-1] / 初始资金 - 1",               "—"],
            ["年化收益", "(1 + 总收益)^(252 / 交易天数) - 1",       "年化因子 252"],
            ["年化波动", "std(日收益率) x sqrt(252)",               "—"],
            ["夏普比率", "(年化收益 - 无风险利率) / 年化波动",       "无风险利率 = 2%"],
            ["最大回撤", "min((equity - cummax) / cummax)",         "—"],
            ["卡玛比率", "年化收益 / |最大回撤|",                    "—"],
            ["日胜率",   "日收益 > 0 的天数 / 总交易天数",          "—"],
        ], [3 * cm, 5.5 * cm, W - 8.5 * cm]),
        SP(6),
    ]

    story += [
        H2("8.2 最优参数回测结果（Top-20，20日调仓，回测区间 2024-09-19 至 2026-04-27）"),
        tbl([
            ["指标",      "策略",    "基准（等权全市场）", "超额"],
            ["总收益率",  "19.84%",  "107.34%",          "-87.50%"],
            ["年化收益率", "12.57%", "61.17%",           "-48.60%"],
            ["年化波动率", "83.06%", "27.71%",           "+55.35%"],
            ["夏普比率",  "0.127",   "2.136",            "-2.009"],
            ["最大回撤",  "-51.25%", "-17.05%",          "-34.20%"],
            ["卡玛比率",  "0.245",   "3.588",            "-3.343"],
            ["日胜率",   "53.5%",   "59.5%",            "-6.0%"],
            ["交易天数",  "385",     "385",               "—"],
            ["总手续费",  "192,303元","—",               "—"],
            ["总交易笔数","742",      "—",               "—"],
        ], [4 * cm, 3 * cm, 4 * cm, 3 * cm],
        warn_rows=[1, 2, 3, 4, 5, 6, 7]),
        Warn("策略全面跑输基准：年化收益率12.57% vs 61.17%，"
             "夏普比率0.127 vs 2.136，最大回撤-51.25% vs -17.05%。"
             "策略实际表现不具备实盘价值。"),
        SP(6),
    ]

    story += [
        H2("8.3 参数敏感性测试"),
        tbl([
            ["top_k", "rebalance_every", "总交易笔数", "总手续费",   "总收益率"],
            ["50",    "5日（约每周）",   "6,000",      "679,504元", "— (较差)"],
            ["20",    "20日（约每月）",  "742",        "192,303元", "19.84%"],
        ], [2 * cm, 4 * cm, 3 * cm, 3.5 * cm, W - 12.5 * cm]),
        Note("换手率是重要成本来源：Top-50 + 5日调仓手续费67万，"
             "Top-20 + 20日调仓仅19万，相差3.5倍。"
             "降低换手率是提升净收益的有效手段之一。"),
        SP(4),
    ]

    # ===== 九、问题诊断与改进方向 =====
    story += [PageBreak(), H1("九、问题诊断与改进方向"), HR()]

    story += [
        H2("9.1 已识别问题清单"),
        tbl([
            ["编号", "问题",                    "证据",                             "严重程度"],
            ["P1",  "模型泛化严重衰退",         "验证IC 0.090，测试IC 0.050，衰减44%", "严重"],
            ["P2",  "策略大幅跑输基准",         "年化12.57% vs 61.17%",              "严重"],
            ["P3",  "基准设计存在偏差",         "等权全市场放大小市值+行情beta效应",   "中"],
            ["P4",  "最大回撤过大",             "-51.25%，风险收益比极差（卡玛0.245）", "严重"],
            ["P5",  "测试期含特殊行情",         "2024Q4 A股政策驱动行情，历史罕见",   "中"],
            ["P6",  "IC 随时间自然衰减",        "训练IC 0.225，测试IC 0.050，衰减78%", "严重"],
            ["P7",  "截面标签实验失败（G2）",   "早停36轮，测试IC 0.025，严重欠拟合",  "中"],
        ], [1.5 * cm, 4 * cm, 5.5 * cm, 2 * cm],
        warn_rows=[1, 2, 4, 6]),
        SP(6),
    ]

    story += [
        H2("9.2 根因假设分析"),
        H3("假设1：时间分布漂移（Domain Shift）"),
        P("训练集（2021-2024）与测试集（2025-2026）的市场状态差异显著。"
          "2024Q4 政策驱动的大行情打破了历史技术面统计规律，"
          "模型未见过该类 regime，导致预测能力大幅下降。"),
        SP(4),
        H3("假设2：标签质量问题"),
        P("固定的 +/-3% 绝对阈值在不同波动率环境下失去区分能力。"
          "高波动期（2024Q4）超过3%的涨跌比例远高于历史均值，"
          "导致标签质量下降，模型学到的模式与实际不符。"),
        SP(4),
        H3("假设3：纯技术因子局限性"),
        P("本模型仅使用技术因子，缺乏基本面因子（PE/PB/ROE）、"
          "资金流因子（北向资金、融资盘）及情绪因子的支撑。"
          "技术因子 IC 天然随时间衰减，单类因子体系稳健性不足。"),
        SP(6),
    ]

    story += [
        H2("9.3 改进方向（优先级排序）"),
        tbl([
            ["优先级", "方向",               "具体措施",                                      "预期效果"],
            ["P0",    "IC 逐月诊断",         "绘制每月 IC 曲线，定位模型从哪个时间点开始失效",  "定位问题根源"],
            ["P1",    "动态阈值标签",        "将固定 +-3% 改为 rolling_std x K 自适应阈值，适应波动率变化", "改善标签质量"],
            ["P2",    "Walk-Forward 验证",   "替换单次 70/15/15，用滚动窗口验证模型时序稳定性", "更真实的策略评估"],
            ["P3",    "Regime 识别",         "引入市场状态变量（趋势/震荡/危机），分状态训练子模型", "提升适应性"],
            ["P4",    "基本面因子融合",      "加入 PE/PB/ROE 等基本面因子，提升 IC 稳定性",   "降低IC衰减"],
            ["P5",    "基准修正",            "改用沪深300或中证500替代等权全市场基准",          "公平评估"],
            ["P6",    "仓位管理优化",        "波动率倒数加权替代等额分配，控制最大回撤",         "改善夏普比率"],
        ], [1.5 * cm, 3.5 * cm, 6 * cm, W - 11 * cm]),
        SP(4),
    ]

    # ===== 附录 =====
    story += [
        PageBreak(), H1("附录：项目文件结构"), HR(),
        Code(
            "~/quant_trading/\n"
            "|\n"
            "+-- config/\n"
            "|   +-- settings.py             # 全局参数（见第一章1.2节）\n"
            "|\n"
            "+-- src/\n"
            "|   +-- data/\n"
            "|   |   +-- pipeline.py         # Phase1 通达信数据提取\n"
            "|   +-- features/\n"
            "|   |   +-- indicators.py       # 30个技术因子计算（见第三章）\n"
            "|   +-- models/\n"
            "|   |   +-- trainer.py          # 时序切分 + LightGBM 训练\n"
            "|   |   +-- evaluator.py        # 评估指标（IC/方向准确率）\n"
            "|   +-- backtest/\n"
            "|       +-- engine.py           # 回测引擎（Top-K + 调仓）\n"
            "|       +-- metrics.py          # 绩效指标（夏普/卡玛/回撤）\n"
            "|\n"
            "+-- scripts/\n"
            "|   +-- g1_no_vol_features.py   # 实验G1：屏蔽波动率因子重训\n"
            "|   +-- g2_cross_sectional_label.py  # 实验G2：截面排名标签\n"
            "|   +-- gen_report_pdf.py       # 本报告生成脚本\n"
            "|\n"
            "+-- data/\n"
            "|   +-- processed/\n"
            "|   |   +-- market_features.parquet  # 6,440,080行 x 51列\n"
            "|   +-- models/\n"
            "|   |   +-- lgbm_model.joblib        # 原模型\n"
            "|   |   +-- eval_results.json        # 原模型评估结果\n"
            "|   |   +-- g1_no_vol/               # G1实验模型及评估结果\n"
            "|   |   +-- g2_rank/                 # G2实验模型及评估结果\n"
            "|   +-- backtest/\n"
            "|       +-- equity_curve.csv         # 净值曲线数据\n"
            "|       +-- trades.csv               # 交易记录明细\n"
            "|       +-- backtest_equity.png      # 净值曲线图\n"
            "|\n"
            "+-- main.py                     # 入口：python3 main.py [1|2|3]"
        ),
        SP(8),
    ]

    return story


# ── 生成 PDF ──────────────────────────────────────────────────────────────────
OUTPUT = "/mnt/c/Users/Administrator/Desktop/A股量化交易模型设计方案.pdf"

doc = SimpleDocTemplate(
    OUTPUT,
    pagesize=A4,
    leftMargin=2 * cm, rightMargin=2 * cm,
    topMargin=2.5 * cm, bottomMargin=2.5 * cm,
    title="A股量化交易模型设计方案",
    author="quant_trading",
)


def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("SimHei", 8)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawString(2 * cm, 1.2 * cm, "A股量化交易模型设计方案 v1.0")
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, "第 %d 页" % doc.page)
    canvas.restoreState()


story = build_story()
doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
print("PDF 已生成：%s" % OUTPUT)
