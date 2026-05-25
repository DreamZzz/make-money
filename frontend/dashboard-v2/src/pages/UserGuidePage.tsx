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
                ["能做", "在权益预算内按相对强弱动态搭配指数基金（沪深300/中证500/恒生科技）"],
                ["能做", "在数据异常、模型不可用、风险过高时阻止你继续调仓"],
                ["不能做", "让你一夜暴富、保证每年正收益、替你真实下单"],
                ["不能做", "保证主动选股跑赢指数——实测现有策略跑输中证500，个股选股目前只作研究/shadow，不占主仓"],
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
              <li>启动 Dashboard V2，打开 `/health` 确认数据、模型、定时任务状态。</li>
              <li>打开 `/today`，看系统推荐下一步。</li>
              <li>打开 `/rebalance`，只关注 `可执行` 和 `需人工确认`。</li>
              <li>手动执行前，确认资金缺口、一手门槛、冲突信号。</li>
              <li>执行后回 `/portfolio`，检查现金、持仓和风险警告。</li>
            </ol>
          </GuideSection>

          <GuideSection id="safety" eyebrow="2" title="入口、安全与隐私">
            <GuideTable
              headers={["入口", "用途"]}
              rows={[
                ["http://localhost:5173/today", "新版散户操作驾驶舱"],
                ["http://localhost:5173/guide", "内置产品使用手册"],
                ["http://localhost:8501", "旧 Streamlit，高级研究/迁移期兜底"],
                ["http://localhost:8600/api/v2/today", "V2 API，不直接给日常用户使用"],
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
                ["今日行动", "/today", "默认首页，判断今天数据能不能用、是否需要调仓"],
                ["调仓执行", "/rebalance", "同屏查看 Core 基金、Satellite 个股、暂缓项和资金缺口"],
                ["组合体检", "/portfolio", "检查现金、持仓、暴露风险和信号收益跟踪"],
                ["市场温度计", "/market", "市场阶段+热度+估值+相对强弱，给 T+1 仓位建议和指数搭配（指数核心阶段最重要）"],
                ["市场与数据健康", "/health", "判断数据源、字段覆盖、任务状态和模型状态是否可用于决策"],
                ["策略竞赛", "/tournament", "多虚拟账户并行对标，竞赛榜+晋级闸门，选最优指导实盘"],
                ["研究实验室", "/research", "收纳 Qlib、IC/ICIR、实验摘要和旧 Streamlit 入口"],
                ["使用手册", "/guide", "查看投资预期、首次使用、复盘阈值、异常处理和术语解释"],
              ]}
            />
            <h3>调仓执行顺序</h3>
            <ol className="guide-steps">
              <li>先看顶部资金缺口。</li>
              <li>再处理 `可执行`。</li>
              <li>然后看 `需人工确认`，特别是基金 REDUCE/BUY。</li>
              <li>`暂缓` 默认不要手动强行执行。</li>
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

          <GuideSection id="weekly" eyebrow="4" title="每周复盘的量化门槛">
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

          <GuideSection id="emergency" eyebrow="5" title="异常场景应急">
            <GuideTable
              headers={["场景", "处理方式"]}
              rows={[
                ["出差 3 天回来", "不补做历史调仓；从今天最新 /today 重新开始。旧信号可能已经过期"],
                ["daily_close 连续失败一周", "停止调仓，先看 /health 的失败步骤；修复后跑完整收盘闭环，再恢复观察"],
                ["券商账户资金冻结或停牌", "纸盘继续记录理论动作，但真实账户按可交易资金执行；下次复盘时记录偏差"],
                ["真实账户与纸盘不一致", "不要硬追纸盘；先手动记录现金流/基金快照，让账户重新对齐"],
                ["换电脑或重装系统", "迁移项目目录，重点保留 data/duckdb/market.db、config/、output/、models/"],
                ["开盘/收盘定时任务没执行", "到 /health 查看定时执行历史；若连续漏跑，优先修调度器，不手动猜信号"],
              ]}
            />
          </GuideSection>

          <GuideSection id="glossary" eyebrow="6" title="术语速查">
            <GuideTable
              headers={["术语", "门外汉一句话", "经验值范围"]}
              rows={[
                ["Core", "指数基金底仓，用来拿市场平均收益和稳定账户", "通常 50-70%"],
                ["Satellite", "个股策略仓，用来争取超额收益", "通常 30-50%"],
                ["satellite_budget", "个股策略本轮还能用多少钱", "BUY 只能在预算内排队"],
                ["Health Ribbon", "页面顶部的数据健康条", "不是“数据可用”就不调仓"],
                ["可执行", "规则和预算内可以执行的计划", "仍需你手动下单"],
                ["暂缓", "系统建议当前不执行", "默认不碰"],
                ["IC", "模型排序和未来收益的相关性", "0.02 算及格，0.05 算优秀，< 0 表示模型可能失效"],
                ["ICIR", "IC 的稳定性", "> 0.3 算可投资，< 0.1 算不稳定"],
                ["confidence", "信号置信度，0-1 越高越可信", "系统默认 0.75 以下不执行"],
                ["一手门槛", "买入 A 股最小 100 股需要的钱", "小资金账户大概率被卡，建议账户 >= 10 万"],
                ["turnover", "换手率，代表本轮买卖占组合的比例", "默认上限 30%/日；过高会被成本侵蚀"],
                ["kill_switch", "回撤过大时的硬止损", "回撤超过 25% 会停止所有 BUY"],
                ["alpha_vs_benchmark", "信号收益减去基准收益", "大于 0 才说明跑赢基准"],
                ["hit rate", "信号命中率", "50-60% 正常，< 45% 警觉"],
              ]}
            />
          </GuideSection>
        </div>
      </div>
    </article>
  );
}
