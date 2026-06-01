import type { ReactNode } from "react";

type GuideTableProps = {
  headers: string[];
  rows: Array<Array<ReactNode>>;
};

function GuideTable({ headers, rows }: GuideTableProps) {
  return (
    <div className="guide-table-wrap">
      <table className="guide-table">
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header}>{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={row.map((cell) => String(cell)).join("-") || rowIndex}>
              {row.map((cell, cellIndex) => (
                <td key={`${rowIndex}-${cellIndex}`}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GuideSection({
  id,
  eyebrow,
  title,
  children,
}: {
  id: string;
  eyebrow: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="guide-section" id={id}>
      <span className="guide-eyebrow">{eyebrow}</span>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

const CONTENT_LINKS = [
  ["开始之前", "#before"],
  ["首次使用", "#onboarding"],
  ["安全边界", "#safety"],
  ["模块动线", "#modules"],
  ["Core 基金扫描", "#funds"],
  ["周复盘", "#weekly"],
  ["异常应急", "#emergency"],
  ["术语速查", "#glossary"],
];

export function UserGuidePage() {
  return (
    <article className="guide-page">
      <header className="page-title-row">
        <div>
          <h1>产品使用手册</h1>
          <p>散户收盘到调仓驾驶舱：投资预期、首次使用、日常操作、复盘门槛和异常处理。</p>
        </div>
        <a className="secondary-link" href="/today">
          返回今日行动
        </a>
      </header>

      <div className="guide-layout">
        <aside className="guide-toc" aria-label="手册目录">
          <strong>手册目录</strong>
          <nav>
            {CONTENT_LINKS.map(([label, href]) => (
              <a href={href} key={href}>
                {label}
              </a>
            ))}
          </nav>
        </aside>

        <div className="guide-content">
          <GuideSection id="before" eyebrow="0" title="在你开始之前">
            <p>
              这套系统不是“神奇荐股器”，而是一套本地化的纪律执行工具。<strong>现阶段以指数为核心</strong>：先判断市场阶段/热度/估值定权益仓位，再按相对强弱搭配指数；主动个股选股因尚未证明可信超额（实测跑输指数），降级为研究/shadow，等市场风格翻转再观察。它的价值是纪律化的择时与搭配，以及用证据诚实判断策略是否值得信任。
            </p>
            <h3>这套系统能为你做什么 / 不能做什么</h3>
            <GuideTable
              headers={["类型", "内容"]}
              rows={[
                ["能做", "自动完成日终数据检查、市场阶段/热度判断、生产信号、资金池约束、纸交易、组合体检和复盘"],
                ["能做", "给出市场阶段+热度的专业判读，并据此给 T+1 增减总仓位建议（目标权益仓位）"],
                ["能做", "在权益预算内按相对强弱动态搭配指数基金（M4 动态权重，沪深300/中证500/恒生科技）"],
                ["能做", "每天扫描 155 支头部 ETF，按六维（趋势/估值/动量/风险/流动性/宏观契合）打分，输出加仓窗口(in_window)、超跌候选(oversold)、高价值关注(watch_high_value) 三类候选"],
                ["能做", "对你持仓基金每天做严格告警：止损(>-5%)、跌穿 MA60、10 日回撤 > 8%、目标偏离 > 20%、同跟踪有更强替代(alternative_available)"],
                ["能做", "区分基金类别 (equity_index / balanced 股债混合 / qdii / commodity) 和你的意图 (active 主动管理 / exited 清仓残留 / watching 观察名单)，按对应口径评估"],
                ["能做", "解析券商截图导出的 JSON 快照，把持仓真值 (cost_price / holding_pnl / day_return) 直接用上，不再让系统按估算口径"],
                ["能做", "在数据异常、模型不可用、风险过高时阻止你继续调仓"],
                ["不能做", "让你一夜暴富、保证每年正收益、替你真实下单"],
                ["不能做", "保证主动选股跑赢指数——实测现有策略跑输中证500，个股选股目前只作研究/shadow，不占主仓"],
                ["不能做", "判断你是否应该真的换基金 (alternative_available 只说\"有更强综合分\"，是否切换看你的成本/账户类型/便利性)"],
                ["不能做", "让你完全不用判断，尤其是基金申赎、现金安排和异常处理"],
              ]}
            />
            <h3>你应该预期什么</h3>
            <GuideTable
              headers={["维度", "合理预期"]}
              rows={[
                ["年化超额收益", "3-8%，对比沪深 300 / 中证 500 / 恒生科技等核心指数"],
                ["单年最大回撤", "20-25%"],
                ["看到效果所需时间", "12 个月以上"],
                ["任意 3 个月短期跑输概率", "约 35%"],
                ["典型 in_window 候选数", "市场普涨期 0-3 支；下跌中段 5-15 支；今日(2026-05-29 高估值期) 1 支"],
                ["典型 oversold 候选数", "市场高位期 0-5 支；下跌后段 10-30 支；今日 13 支(港股科技/医药/煤炭/酒类等)"],
                ["最适合的资金", "10 万元以上更顺畅；5 万元可用但会频繁遇到一手门槛"],
              ]}
            />
            <h3>什么情况下应该停止跟单</h3>
            <GuideTable
              headers={["停止条件", "动作"]}
              rows={[
                ["连续 6 个月跑输基准 >= 5 个百分点", "暂停新增 BUY，只保留风险管理和复盘"],
                ["连续 3 个 daily_close 失败且未修复", "暂停调仓，先修数据/任务链路"],
                ["半年内有明确用钱需求", "降低仓位或退出，不要让投资系统和现金需求冲突"],
                ["生产模型监控出现 CRITICAL 且持续未解决", "不跟 Alpha158 新信号，只做组合体检"],
              ]}
            />
          </GuideSection>

          <GuideSection id="onboarding" eyebrow="1" title="第一次使用：从零到首次调仓">
            <p>首次使用不要急着一次买满。先让系统跑通，再逐步把真实账户和纸盘对齐。</p>
            <GuideTable
              headers={["问题", "建议"]}
              rows={[
                ["最低多少资金值得用？", "5 万元可以试用，但一手门槛会卡住很多 A 股；10 万元以上体验明显更好"],
                ["已有持仓怎么处理？", "不建议立刻清仓重来；先导入或记录当前持仓，让系统在组合体检里识别风险"],
                ["第一次信号是否一次性买齐？", "不建议。第一次只执行高置信度、预算内、无冲突的 30-50%，观察 2-4 周"],
                ["三个资金档位怎么选？", "small <= 10 万，medium 10-50 万，large >= 50 万"],
                ["启动后第几天的信号可以信？", "至少等 1 次完整收盘闭环 + 1 次开盘纸交易成功后，再开始参考"],
              ]}
            />
            <ol className="guide-steps">
              <li>启动 Dashboard V2，打开 `/health` 确认数据可决策状态 (C1 三档：可决策 / 备源接管 / 失败)。</li>
              <li>打开 `/today`，看市场阶段、目标权益仓位、Core 基金摘要。</li>
              <li>打开 `/funds`，录入今日持仓快照 (份额/成本，或直接粘 broker 截图导出的 JSON)，看每日评估和告警。</li>
              <li>打开 `/rebalance`，只关注 `可执行` 和 `需人工确认`。</li>
              <li>手动执行前，确认资金缺口、一手门槛、冲突信号。</li>
              <li>执行后回 `/portfolio`，检查现金、持仓和风险警告。</li>
            </ol>
            <h3>持仓快照怎么录入</h3>
            <p>
              系统对基金的所有评估依赖 `index_fund_snapshots` 表。两种录入方式：
            </p>
            <GuideTable
              headers={["方式", "操作", "适用场景"]}
              rows={[
                ["简易手填", "调用 POST /api/v2/index-fund-snapshots，提交 fund_code + shares + cost_amount", "新建持仓、调仓后份额变更"],
                ["broker JSON 粘贴", "把券商截图 OCR 后的 JSON 写到 note 字段，系统会自动升格 market_value / cost_price / holding_pnl / holding_return_pct / day_return_pct / holding_days / yesterday_pnl", "已有真实持仓、想让系统用 broker 真值校验 shares×nav"],
              ]}
            />
            <p>
              broker JSON 推荐字段 (键名固定，系统按这个解析)：
              <code style={{ display: "block", padding: 8, marginTop: 4, fontSize: 11, background: "var(--surface-elevated, rgba(255,255,255,0.04))" }}>
                {`{"source":"manual_broker_screenshot","captured_at":"2026-05-30 19:33","nav_date":"2026-05-29","market_value":105088.64,"latest_nav":1.1636,"cost_price_display":1.218,"holding_pnl":-4911.36,"holding_return_pct":-0.0446,"day_return_pct":-0.0018,"yesterday_pnl":-189.66,"holding_days":92}`}
              </code>
            </p>
          </GuideSection>

          <GuideSection id="safety" eyebrow="2" title="入口、安全与隐私">
            <GuideTable
              headers={["入口", "用途"]}
              rows={[
                ["http://localhost:5173/today", "市场驾驶舱(每日入口)"],
                ["http://localhost:5173/funds", "Core 基金评估 + 推荐 + 持仓告警"],
                ["http://localhost:5173/market", "市场温度计:阶段/热度/估值/相对强弱"],
                ["http://localhost:5173/rebalance", "调仓执行:Core sleeve + Satellite 个股"],
                ["http://localhost:5173/portfolio", "组合体检"],
                ["http://localhost:5173/health", "今天可决策吗 + 数据源分级"],
                ["http://localhost:5173/guide", "内置产品使用手册"],
                ["http://localhost:8501", "旧 Streamlit，高级研究/迁移期兜底"],
                ["http://localhost:8600/api/v2/funds", "V2 API 基金接口，含 in_window/oversold/watch 推荐 + holding_alerts"],
              ]}
            />
            <p>
              V2 首期只做安全写入，不会真实下单，不会从前端启动收盘闭环、开盘纸交易或研究任务，也不会允许直接切换 production 模型、手动改信号状态或绕过风控规则。
            </p>
            <p>
              默认数据保存在本机 DuckDB：`data/duckdb/market.db`。Dashboard V2 不会向外部服务上传持仓、现金流、信号或纸交易记录。AkShare、yfinance、Baostock 等只作为行情、财务和成分股下载数据源。
            </p>
            <p>
              当前默认定位是“日终在本机电脑查看”。如果出差需要查看，优先通过可信 VPN 或 SSH tunnel 暴露本机端口，不要直接把 5173/8600 暴露到公网。
            </p>
          </GuideSection>

          <GuideSection id="modules" eyebrow="3" title="一级模块怎么用">
            <GuideTable
              headers={["模块", "路径", "用途"]}
              rows={[
                ["市场驾驶舱", "/today", "默认首页，看市场阶段/目标仓位/Core 基金摘要/今日待办"],
                ["市场温度计", "/market", "市场阶段+热度+估值+相对强弱，给 T+1 仓位建议和指数搭配（指数核心阶段最重要）"],
                ["Core 基金", "/funds", "三大区：持仓告警卡 + in_window 可加仓 + oversold/watch 关注名单。每天 18/18 自动跑"],
                ["调仓执行", "/rebalance", "同屏查看 Core 基金、Satellite 个股、暂缓项和资金缺口"],
                ["组合体检", "/portfolio", "检查现金、持仓、暴露风险和信号收益跟踪"],
                ["市场与数据健康", "/health", "今天可决策吗 + 数据源分级(decidable/backup_active/failed) + 定时任务状态"],
                ["策略竞赛", "/tournament", "多虚拟账户并行对标，竞赛榜+晋级闸门，选最优指导实盘"],
                ["研究实验室", "/research", "收纳 Qlib、IC/ICIR、实验摘要和旧 Streamlit 入口"],
                ["使用手册", "/guide", "查看投资预期、首次使用、复盘阈值、异常处理和术语解释"],
              ]}
            />
            <h3>调仓执行顺序</h3>
            <ol className="guide-steps">
              <li>先看顶部资金缺口和"本轮预算根因卡"。</li>
              <li>再处理 `可执行`。</li>
              <li>然后看 `需人工确认`，特别是基金 REDUCE/BUY。看到 Core sleeve 时，可点链接跳 `/funds` 看每支基金详情。</li>
              <li>`暂缓` 默认不要手动强行执行。</li>
              <li>如果 `/funds` 持仓告警里有 critical (stop_loss) 或 alternative_available，独立判断是否调整。</li>
              <li>如果出现冲突信号，优先不做，等下一轮信号确认。</li>
            </ol>
            <h3>如何读信号收益跟踪</h3>
            <GuideTable
              headers={["指标", "怎么判断"]}
              rows={[
                ["5 日 alpha", "信号发出后约一周相对基准的超额收益，适合看短期执行质量"],
                ["20 日 alpha", "信号发出后约一个月相对基准的超额收益，更适合判断策略有效性"],
                ["alpha_vs_benchmark", "原始收益减去对应基准收益；大于 0 才说明不是单纯吃到市场上涨"],
                ["hit rate", "50-60% 算正常，低于 45% 要警觉"],
                ["model_name", "观察 trend / mean_rev / industry_rotation / alpha158 / value_quality 谁在贡献超额"],
              ]}
            />
          </GuideSection>

          <GuideSection id="funds" eyebrow="4" title="Core 基金扫描与推荐怎么读">
            <p>
              `/funds` 是 D/E/F/G 阶段(2026-05-30~31) 完整重构的基金闭环。所有数据每日收盘后自动刷新 (daily_close step 17b 拉 155 支候选池 nav，step 18 跑 scanner+monitor+recommendations)，下次打开即看当日实时。
            </p>

            <h3>三大区怎么解读</h3>
            <GuideTable
              headers={["区域", "你看到什么", "怎么用"]}
              rows={[
                ["持仓告警卡 (顶部)", "critical / warning / info 三档计数 + 明细列表，按持仓基金分组", "critical 必看(止损线已破)；warning 留意(MA60/回撤/欠配)；info 是参考(趋势弱/推荐替代/加仓窗口开)"],
                ["持仓评估卡 (中部，每支基金一张)", "broker 真值持仓 + 收益 + 估值分位 + 趋势 + M4 目标 + 应执行金额 + 告警 ribbon", "卡片顶端 category/intent badge 决定评估口径(balanced 类不算估值/M4 强对齐；exited 灰显不算 delta)"],
                ["今日可加仓窗口 (in_window)", "扫描器认为同时满足趋势健康+估值中位+宏观契合的候选；Top 5", "今天系统认为可以下手的；点开看 thesis 和 6 维分数"],
                ["超跌候选 (oversold)", "估值 < 30% 分位 + 已回撤 > 20% 但趋势还弱；Top 10", "不是\"今天可加仓\"，是\"等趋势(MA120/250)转头再考虑分批\"的关注名单"],
                ["高价值关注 (watch)", "综合分 ≥ 70 但当前估值偏贵或趋势未确认；Top 10 折叠", "等价值窗口或确认信号；不要立即加仓"],
              ]}
            />

            <h3>六维评分怎么读</h3>
            <GuideTable
              headers={["维度", "权重", "判断"]}
              rows={[
                ["趋势 (trend)", "30%", "close vs MA120/250 + 多头排列；100 = 全站稳 + 多头排列；10 = 跌穿"],
                ["估值 (valuation)", "20%", "1 − 价格在 3 年内分位；100 = 10 年最便宜；0 = 10 年最贵；30 以下偏贵"],
                ["动量 (momentum)", "15%", "1/3/6 月平均收益映射；50 = 持平；100 = 半年涨 +15%；0 = 半年跌 -15%"],
                ["风险 (risk)", "15%", "年化波动 + 最大回撤；vol < 15% 满分，> 40% 0 分；dd 0 → +30，-50% → -30"],
                ["流动性 (liquidity)", "10%", "规模适中曲线；50 亿 = 50 分，200-500 亿 = 90+，> 1000 亿 略减为 70"],
                ["宏观契合 (macro)", "10%", "市场 stage × 基金 category；危机期商品/债反向加分"],
              ]}
            />

            <h3>signal_tag 的优先级</h3>
            <p>
              scanner 给每支基金打一个 signal_tag。优先级从高到低：<br />
              <code>insufficient_data → avoid(估值 &gt; 85%) → oversold_candidate → avoid(趋势破) → in_window → watch_high_value → neutral</code>
            </p>
            <p>
              注意 oversold_candidate 在 trend_broken 的 avoid 之前判断 — 估值已在底部 + 已深度回撤的标的，不算"规避"，算"等抄底"。
            </p>

            <h3>F4-v2 智能 overlap_tracking</h3>
            <p>
              推荐引擎默认排除"同跟踪指数"的候选(避免推一支你已经持有的)，但有三种放行条件：
            </p>
            <GuideTable
              headers={["放行规则", "thesis 标记"]}
              rows={[
                ["持仓在此跟踪指数全 exited", "持仓已退出，可重新进入"],
                ["active 持仓欠配 (current < target × 0.8)", "欠配补仓: 持仓 X 万 / 目标 Y 万 (缺 Z 万)"],
                ["候选综合分 > 持仓最高 + 5", "超额表现: 综合分 N > 持仓最高 M +K"],
              ]}
            />

            <h3>持仓告警 6 类</h3>
            <GuideTable
              headers={["alert_type", "level", "触发条件", "suggested_action"]}
              rows={[
                ["stop_loss", "critical", "broker holding_return_pct < -5%", "exit_stop_loss"],
                ["ma60_break", "warning", "最新 nav < MA60", "reduce_partial"],
                ["drawdown_10d", "warning", "近 10 日回撤 > 8%", "reduce_partial"],
                ["target_drift", "warning", "账户级权重偏离 M4 目标 > 20%", "reduce_partial 或 add_window_open"],
                ["trend_weak", "info", "nav < MA120", "monitor"],
                ["alternative_available", "info", "同跟踪指数有综合分高 +5 的替代", "consider_switch"],
                ["add_window_open", "info", "扫描器判定该基金本身进入 in_window", "add_window_open"],
              ]}
            />

            <h3>category 和 intent 怎么影响评估</h3>
            <GuideTable
              headers={["category", "怎么处理"]}
              rows={[
                ["equity_index / broad", "适用全部六维 + M4 RS 池"],
                ["qdii", "适用全部六维 + M4 RS 池(港股/海外标的)"],
                ["balanced (股债混合)", "跳过 price_pct / MA / M4 强制对齐；只展示 holding PnL；不进 RS 池"],
                ["commodity / bond", "macro 维度反向(危机/下跌期反向加分)；不进 RS 池"],
              ]}
            />
            <GuideTable
              headers={["intent", "怎么处理"]}
              rows={[
                ["active", "M4 RS 池正常参与；告警/评估全套；推荐去重以此为基准"],
                ["watching", "不持仓但加入候选池；F4 推荐时 thesis 末尾标 '已在你的观察名单'，UI 显示 [已观察] 徽章"],
                ["exited", "持仓灰显；不算 delta_amount；不参与 M4；overlap_tracking 也按已退出处理(允许同跟踪重入)"],
              ]}
            />
          </GuideSection>

          <GuideSection id="weekly" eyebrow="5" title="每周复盘的量化门槛">
            <p>周五收盘后或周末做一次复盘。出现以下条件时，优先降低操作频率，而不是继续加仓。</p>
            <GuideTable
              headers={["信号", "定量门槛", "建议动作"]}
              rows={[
                ["IC/ICIR 变差", "60 日滚动 ICIR 从基线下降 > 50%，持续 2 周", "降低 Alpha158 权重，等待重训或新实验"],
                ["暂缓项增加", "暂缓比例从历史均值 30% 上升到 > 60%，连续 2 周", "检查资金、估值、规则是否过紧"],
                ["信号冲突变多", "每周冲突数从 0-1 上升到 >= 3，持续 2 周", "暂停冲突标的，不手动猜方向"],
                ["纸盘落后指数", "30 日累计落后基准 >= 5 个百分点", "降低操作频率，复核模型和成本"],
                ["组合风险警告增加", "CRITICAL 警告 >= 2 个，持续 3 天", "不新增 BUY，先处理集中度和现金"],
              ]}
            />
          </GuideSection>

          <GuideSection id="emergency" eyebrow="6" title="异常场景应急">
            <GuideTable
              headers={["场景", "处理方式"]}
              rows={[
                ["出差 3 天回来", "不补做历史调仓；从今天最新 /today 重新开始。旧信号可能已经过期"],
                ["daily_close 连续失败一周", "停止调仓，先看 /health 的失败步骤；修复后跑完整收盘闭环，再恢复观察"],
                ["券商账户资金冻结或停牌", "纸盘继续记录理论动作，但真实账户按可交易资金执行；下次复盘时记录偏差"],
                ["真实账户与纸盘不一致", "不要硬追纸盘；先手动记录 broker JSON 快照到 /funds，让 evaluator 用真值重新对齐"],
                ["基金快照过期 (snapshot_stale)", "/funds 卡片显示 ⚠ 快照 Nd 未更新时，立即录入今日份额；> 3 天 evaluator 会标 snapshot_stale 风险"],
                ["持仓出现 alternative_available", "info 级别，不强制换。先确认替代品费率/账户类型/便利度；如有持仓在 active 同时有 +10 分以上的 ETF，考虑下次定投切换"],
                ["扫描器某天 0 候选", "正常 — 市场高位期或全面下跌期都可能 0 in_window；不要硬找，看 oversold / watch 等下一个窗口"],
                ["scanner 表 fund_screening_results 没数据", "可能候选池 nav 未拉。手动 bash scripts/refetch_etf_nav.sh 或 python -m src.data_pipeline.fund_etf_provider fetch --nav-only"],
                ["eastmoney 数据源被反爬熔断", "F1 多源 fallback 已自动切到 sina；如 sina 也失败，看 /health 的 data_source_health 卡片(C1 分级会显示 backup_active 或 failed)"],
                ["换电脑或重装系统", "迁移项目目录，重点保留 data/duckdb/market.db、config/、output/、models/"],
                ["开盘/收盘定时任务没执行", "到 /health 查看定时执行历史；若连续漏跑，优先修调度器，不手动猜信号"],
              ]}
            />
          </GuideSection>

          <GuideSection id="glossary" eyebrow="7" title="术语速查">
            <h3>仓位结构</h3>
            <GuideTable
              headers={["术语", "门外汉一句话", "经验值范围"]}
              rows={[
                ["Core", "指数基金底仓，用来拿市场平均收益和稳定账户", "现阶段以指数为核心，通常 50-87%"],
                ["Satellite", "个股策略仓，用来争取超额收益", "降级为 shadow，目前几乎不占主仓"],
                ["satellite_budget", "个股策略本轮还能用多少钱", "BUY 只能在预算内排队"],
                ["equity_exposure (target)", "宏观目标权益仓位", "强势上升 87%、震荡 60%、危机 25%"],
                ["account_total_value", "你账户总资产 (现金 + 持仓市值)", "evaluator 用它算每支基金的目标值"],
              ]}
            />
            <h3>基金扫描器输出</h3>
            <GuideTable
              headers={["术语", "门外汉一句话", "经验值范围"]}
              rows={[
                ["signal_tag", "scanner 给基金的分类标签", "in_window / oversold_candidate / watch_high_value / avoid / neutral / insufficient_data"],
                ["in_window", "今天可加仓 (趋势+估值+宏观三者达标)", "市场普涨期 0-3 支；高位期罕见"],
                ["oversold_candidate", "估值低 + 已深度回撤 (等趋势反转)", "下跌后段 10-30 支；高位期少"],
                ["watch_high_value", "综合分高但估值偏贵或趋势未确认", "Top 10 折叠列表"],
                ["avoid", "估值 > 85% 或趋势已破", "市场高位期占大多数"],
                ["total_score", "六维加权综合分", "0-100；> 70 算优秀候选；< 40 算规避"],
                ["price_pct", "价格在 3 年 nav 历史中的分位", "< 30% 便宜；> 85% 偏贵"],
                ["trend_score", "趋势子分", "100 = 多头排列全站稳；10 = 跌穿"],
                ["macro_score", "宏观契合分", "依市场 stage × 基金类别；危机期商品/债反向加分"],
              ]}
            />
            <h3>持仓告警 / 推荐</h3>
            <GuideTable
              headers={["术语", "门外汉一句话", "动作"]}
              rows={[
                ["stop_loss", "持仓收益破 -5% 止损线", "critical → exit_stop_loss"],
                ["ma60_break", "nav 跌穿 60 日均线", "warning → reduce_partial"],
                ["drawdown_10d", "近 10 日回撤超 8%", "warning → reduce_partial"],
                ["target_drift", "持仓权重偏离 M4 目标 > 20%", "warning → reduce_partial 或 add_window_open"],
                ["alternative_available", "同跟踪指数有综合分 +5 以上的更强 ETF", "info → consider_switch (自己评估费率/便利)"],
                ["overlap_tracking", "推荐与持仓同跟踪指数的关系", "F4-v2 智能比较，欠配/超额/exited 三条放行规则"],
                ["broker_mismatch", "broker 给的市值 vs shares × nav 偏差 > 1%", "看数据来源是否过期"],
              ]}
            />
            <h3>基金分类与意图</h3>
            <GuideTable
              headers={["术语", "门外汉一句话"]}
              rows={[
                ["category", "基金类别：equity_index/broad 宽基 ETF / qdii 海外 / balanced 股债混合 / commodity 商品 / bond 债券"],
                ["intent", "你的意图：active 主动管理 / exited 已清仓残留 / watching 加入观察未持仓"],
                ["balanced 类", "evaluator 不算 price_pct/MA/M4 对齐；只展示 holding PnL"],
                ["exited 状态", "持仓灰显；不算 delta_amount；不进 M4 RS 池；overlap_tracking 允许同跟踪重入"],
                ["watching 状态", "不持仓但加入候选池；推荐时 thesis 标 '已在你的观察名单'，UI 显示 [已观察]"],
              ]}
            />
            <h3>数据源 / 调度</h3>
            <GuideTable
              headers={["术语", "门外汉一句话", "经验值"]}
              rows={[
                ["data_source_health", "数据源每日同步状态", "decidable / backup_active / degraded / failed 四档"],
                ["M3 (target_exposure)", "T+1 目标权益仓位", "依市场 stage + 估值/宽度/热度微调"],
                ["M4 (index_allocation)", "动态指数搭配权重", "按相对强弱 (RS) 在 active equity/qdii 之间分配 equity_budget"],
                ["scheduler_runs", "watchdog 每次终态结构化记录", "取代 cron.log 文本解析"],
                ["scanner_total_score (历史)", "fund_screening_results 表，每日一行 / 基金", "未来用于回测信号转化率"],
              ]}
            />
            <h3>策略 / 模型</h3>
            <GuideTable
              headers={["术语", "门外汉一句话", "经验值范围"]}
              rows={[
                ["Health Ribbon", "页面顶部的数据健康条", "不是 decidable 就不调仓"],
                ["可执行", "规则和预算内可以执行的计划", "仍需你手动下单"],
                ["暂缓", "系统建议当前不执行", "默认不碰"],
                ["IC", "模型排序和未来收益的相关性", "0.02 及格，0.05 优秀，< 0 失效"],
                ["ICIR", "IC 的稳定性", "> 0.3 算可投资，< 0.1 不稳定"],
                ["confidence", "信号置信度，0-1 越高越可信", "系统默认 0.75 以下不执行"],
                ["一手门槛", "买入 A 股最小 100 股需要的钱", "小资金账户大概率被卡，建议账户 >= 10 万"],
                ["turnover", "换手率，本轮买卖占组合的比例", "默认上限 30%/日；过高会被成本侵蚀"],
                ["kill_switch", "回撤过大时的硬止损", "回撤超过 25% 会停止所有 BUY"],
                ["alpha_vs_benchmark", "信号收益减去基准收益", "大于 0 才说明跑赢基准"],
                ["hit rate", "信号命中率", "50-60% 正常，< 45% 警觉"],
                ["NO_HOLDING_SKIP", "无持仓 SELL 信号前置拒收 (B2)", "套利层每天清掉 60-90 条噪音"],
              ]}
            />
          </GuideSection>
        </div>
      </div>
    </article>
  );
}
