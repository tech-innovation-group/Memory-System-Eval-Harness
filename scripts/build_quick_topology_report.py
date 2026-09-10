"""Render persisted four-topology observations without making model calls."""
import argparse
import html
import json
from collections import Counter
from pathlib import Path

NAMES = {'baseline': '共享低负载基线', 'users': '① 多用户 · 单会话串行',
         'sessions': '② 四用户 · 多会话串行', 'session-concurrent': '③ 四用户 · 单会话并发',
         'mixed': '④ 四用户 · 大小写入与检索混合'}


def scene_label(scene):
    if scene['name'] == 'mixed':
        return f'④ {scene["users"]}用户 · 大小请求混合'
    return (f'{scene["users"]}用户 × 每用户{scene["sessions"]}会话'
            f' × 会话内{scene["session_width"]}并发')


def esc(value):
    return html.escape(str(value))


def number(value, places=2):
    return '未采集' if value is None else f'{value:.{places}f}'


def jain(values):
    if len(values) < 2 or any(v is None for v in values):
        return None
    den = len(values) * sum(v*v for v in values)
    return sum(values)**2 / den if den else None


def group_fairness(scene, indices):
    users = scene['per_user']
    selected = [users[str(i)] for i in indices if str(i) in users]
    commits = [u['commit']['commit_completed'] for u in selected]
    p95 = [u['search']['p95_ms'] for u in selected]
    inverse = [1/v if v and v > 0 else None for v in p95]
    return jain(commits), jain(inverse)


def render(state, manifest=None, diagnostics=None, resources=None, models=None):
    scenes = state.get('scenes', [])
    manifest = manifest or {}
    seed = state.get('seed_quality', {})
    status = {'BLOCKED_UNRESOLVED_BACKLOG': '已采集全部已列场景；存在Commit观察超时，未全部通过',
              'FINISHED': '采集结束（不代表业务全部通过）'}.get(state.get('status'), state.get('status'))
    header = f'<h1>四类并发拓扑 · 实测报告</h1><p class="subtitle">{len(scenes)}/10 组已测 · {esc(status)}</p>'
    header += '<p>比较用户数、会话数、会话内并发与大小写入干扰。这里只报告短测数据，不宣称最大容量。</p>'
    if manifest:
        header += f'<p>服务器容器：{esc(manifest.get("cpu", "未采集"))} CPU / {esc(manifest.get("memory_gib", "未采集"))} GiB；场景总耗时（含预热、末尾排空，不含准备）{sum(s["elapsed_s"] for s in scenes)/60:.1f} 分钟。</p>'
    if models:
        header += '<p>实际模型：' + '；'.join(f'{esc(k)} = {esc(v.get("model"))}' for k,v in models.items()) + '</p>'
    if resources:
        cpus = [float(r['CPUPerc'].rstrip('%')) for r in resources if r.get('CPUPerc')]
        header += f'<p>资源采样 {len(resources)} 次，CPU采样峰值 {max(cpus) if cpus else 0:.1f}%（400%约等于四核）；最后采样内存 {esc(resources[-1].get("MemUsage"))}。全程统计包含准备，不作单场景因果结论。</p>'
    measured = [s for s in scenes if s['name'] != 'mixed' and s['search']['p95_ms'] is not None]
    if measured:
        worst = max(measured, key=lambda s: s['search']['p95_ms'])
        header += f'<h2>总体结论</h2><p>已测 Search 场景中最高 P95 为 {worst["search"]["p95_ms"]/1000:.2f} 秒，出现在「{esc(NAMES[worst["name"]])} / {worst["level"]}」。请同时看健康召回率；延迟低不意味着检索正确。</p>'
    if seed:
        header += f'<div class="warning">种子写入完成 {seed["completed"]}/{seed["total"]}；最终事实命中 {seed["hits"]}/{seed["total"]}；健康命中 {seed["healthy"]}/{seed["total"]}。未命中和降级均保留，接口响应速度不能当作健康召回性能。</div>'
    rows = ''
    for scene in scenes:
        s, c = scene['search'], scene['commit']
        rows += '<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in (
            scene_label(scene), scene['level'], scene['http_peak'], s['offered'],
            number(s['p95_ms']), f'{s["search_quality_ok"]}/{s["offered"]}',
            f'{c["commit_completed"]}/{c["offered"]}' if c['offered'] else '未测', c['commit_failed'], c['commit_timed_out'])) + '</tr>'
    table = '<section><h2>先弄清楚：用户、会话、串行</h2><p><b>用户</b>是独立测试身份；本轮每个用户对应独立租户凭据。<b>会话（Session）</b>可以理解为该用户打开的一段对话。</p><p><b>会话内串行</b>：同一Session发出请求A后，等A返回或超时，才发请求B。不同Session、不同用户仍然可以同时请求。这里不表示服务端内部串行。</p><p><b>单会话串行</b>：每用户只有1个Session，每个Session最多1个在途请求。例：16用户各发1个请求，总共最多16个并发。</p><p><b>多会话串行</b>：每用户有多个Session，各Session内部串行，但Session之间并行。例：4用户各4个Session，每个Session发1个请求，总共最多16个并发。</p><p><b>单会话内并发</b>：同一Session允许多个请求同时在途。例：4用户各1个Session，每个Session同时发4个请求，总共最多16个并发。</p></section>'
    table += '<section><h2>先看总体数据</h2><div class="scroll"><table><thead><tr>'
    table += ''.join(f'<th>{label}</th>' for label in ('场景', '配置并发', 'HTTP峰值', 'Search数', 'Search P95 ms', '健康召回', 'Commit完成', 'Commit失败', '观察超时'))
    table += f'</tr></thead><tbody>{rows}</tbody></table></div><p class="note">前三类按确认方案仅压测Search，第四类为Search/Commit混合，不代表前三类Commit也已覆盖。配置并发是客户端工作名额，HTTP 峰值是实际请求数；Commit 等待轮询期间并不一直占用 HTTP 连接。完成分母是调用数，不自动等同于独立任务数。</p></section>'
    charts = ''
    metrics = [('Search P95 / 毫秒', lambda x: x['search']['p95_ms'], '#197d91'),
               ('健康召回占比 / %', lambda x: 100*x['search']['search_quality_ok']/x['search']['offered'] if x['search']['offered'] else None, '#247853')]
    for title, metric, color in metrics:
        maximum = max([metric(s) or 0 for s in scenes] + [1])
        bars = ''
        for scene in scenes:
            value = metric(scene)
            bars += f'<div class="bar"><span>{esc(scene_label(scene))} / 总{scene["level"]}</span><div class="track"><i style="width:{100*(value or 0)/maximum:.2f}%;background:{color}"></i></div><b>{number(value)}</b></div>'
        charts += f'<article><h3>{title}</h3>{bars}</article>'
    detail = ''
    for scene in scenes:
        s, c = scene['search'], scene['commit']
        per = ''
        for user, data in scene['per_user'].items():
            a, b = data['search'], data['commit']
            elapsed = max(scene['elapsed_s'] - (0 if scene['name'] == 'mixed' else 10), .001)
            per += '<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in (f'U{int(user)+1}', a['offered'], number(a['p95_ms']),
                f'{a["search_quality_ok"]}/{a["offered"]}', number(a['search_quality_ok']/elapsed),
                b['offered'], b['commit_completed'], number(b['commit_completed']/elapsed), b['commit_timed_out'])) + '</tr>'
        method = f'{scene["users"]} 个独立用户，每用户 {scene["sessions"]} 个 Search 会话，单会话 {scene["session_width"]} 个配置名额。'
        if scene['name'] != 'mixed':
            method += f'共 {scene["users"] * scene["sessions"]} 个Session；总配置并发 = {scene["users"]} × {scene["sessions"]} × {scene["session_width"]} = {scene["level"]}。'
            method += ('每个Session等前一个请求返回或超时才发下一个；不同Session同时执行。'
                       if scene['session_width'] == 1 else '同一Session允许多个请求同时执行，不等前一个返回再发下一个。')
        fairness = ''
        if scene['name'] == 'mixed':
            method = '4 用户，Search 与 Commit 各占一半独立工作名额。U1/U2 写入512字符，U3/U4写入4096字符。所有用户轮转参与Search和Commit；每个Commit使用新会话，避免合并复用。低并发下用户不会同时全活跃。'
            for label, group in [('短写入组 U1/U2', (0,1)), ('长写入组 U3/U4', (2,3))]:
                cj, sj = group_fairness(scene, group)
                fairness += f'<p>{label}：完成吞吐 Jain <b>{number(cj,4)}</b>；Search逆P95 Jain <b>{number(sj,4)}</b>。</p>'
            fairness += '<p class="note">Jain越接近1表示同组更均衡，不表示性能快。没有样本或全零时不计算。此处为闭环短测，不保证各用户到达率相等，不能单独证明服务端调度公平。</p>'
            for label, op, key in [('每用户 Commit 完成数', 'commit', 'commit_completed'),
                                   ('每用户 Search P95 / ms', 'search', 'p95_ms')]:
                maximum = max([u[op].get(key) or 0 for u in scene['per_user'].values()] + [1])
                fairness += f'<h3>{label}</h3>'
                for user, data in scene['per_user'].items():
                    value = data[op].get(key)
                    size = '短写入' if int(user) < 2 else '长写入'
                    color = '#197d91' if int(user) < 2 else '#ad6420'
                    fairness += f'<div class="bar"><span>U{int(user)+1} · {size}</span><div class="track"><i style="width:{100*(value or 0)/maximum:.2f}%;background:{color}"></i></div><b>{number(value)}</b></div>'
        detail += f'<section><h2>{esc(NAMES[scene["name"]])} · {scene["users"]}用户 · 总{scene["level"]}并发</h2><p>{method}</p>'
        detail += f'<p>正式提交窗口 {scene["measurement_s"]} 秒；含预热及排空总耗时 {scene["elapsed_s"]:.1f} 秒。Search HTTP分布 {esc(s["http_counts"])}；降级 {s["search_degraded"]} 次。Commit接收 {c["commit_accepted"]} 次，独立归档 {c["commit_unique_archives"]} 个。</p>'
        detail += f'<p>Search P50/P95/P99：{number(s["p50_ms"])}/{number(s["p95_ms"])}/{number(s["p99_ms"])} ms；Commit操作 P95：{number(c["p95_ms"])} ms（包括提交和等待，不是模型耗时）。</p>'
        if 'healthy_p95_ms' in scene:
            detail += f'<p>仅健康召回样本 P95：{number(scene["healthy_p95_ms"])} ms，样本 {s["search_quality_ok"]}/{s["offered"]}；这是成功子集延迟，不能替代全请求指标。</p>'
        detail += fairness + '<details><summary>展开每用户数据</summary><div class="scroll"><table><tr><th>用户</th><th>Search数</th><th>P95 ms</th><th>健康命中</th><th>健康召回/s</th><th>Commit数</th><th>完成</th><th>完成/s</th><th>超时</th></tr>' + per + '</table></div></details><p class="note">吞吐分母为正式窗口加末尾排空耗时；HTTP峰值含预热。Commit失败和观察超时均未从调用分母排除。</p></section>'
    evidence = '<section><h2>边界与问题归属</h2><p>HTTP200不代表召回健康，HTTP202不代表写入完成，90秒观察超时不代表服务端failed。种子质量不足时，本轮只能作为含降级的真实链路数据。服务日志、模型阶段耗时、CPU/内存需与本轮采样对齐，不使用旧报告数值补齐。</p><p>前三类共享4并发基线；场景顺序固定，缓存热度与上一阶段未结束的服务端请求可能影响后续场景，不能当作完全独立的A/B实验。HTTP峰值不包括会话创建。大小写入使用重复事实构造不同长度，不代表所有真实文本复杂度。</p>'
    if diagnostics:
        errors = Counter(row.get('evidence', {}).get('error_type') for row in diagnostics.get('samples', []) if row.get('evidence', {}).get('error_type'))
        evidence += f'<h3>本轮服务日志错误类型</h3><p>{esc(dict(errors))}</p><p class="note">这是采集到的事件数，不是独立失败请求数；采样上限或日志截断会影响计数。不能仅凭这些事件认定全部超时或降级同因。</p>'
        events = diagnostics.get('events', {})
        evidence += f'<p>全日志事件计数：引擎事件失败 {events.get("engine_event_failed", "未采集")}；Recall LLM失败 {events.get("recall_llm_failed", "未采集")}；Commit完成 {events.get("commit_completed", "未采集")}（含种子）。详细事件最多保留 {diagnostics.get("sample_cap", "未采集")} 条，因此错误类型样本数可能小于全量事件数。</p>'
        evidence += '<p>改进方向：Memory Unit引擎核查发布状态与接管初始化；Recall/意图路由保留上游HTTP状态和错误码，区分限流与其他模型异常；入口/调度记录每阶段排队与执行时间；测试平台采用按事件分桶采样，并对超时任务继续逐笔核对终态。全局完成事件数不能代替每个超时任务的匹配证据。</p>'
    evidence += f'<details><summary>运行版本与环境证据</summary><pre>{esc(json.dumps(manifest,ensure_ascii=False,indent=2))}</pre></details><p><a href="quick-matrix.json">原始汇总数据</a> · <a href="manifest.json">运行清单</a></p></section>'
    css = 'body{font:16px/1.6 system-ui;margin:32px auto;padding:0 24px;max-width:1200px;color:#202a26;background:#f9fbfa}h1{font-size:32px}h2{font-size:23px}section{padding:24px 0;border-top:1px solid #ccd7d0}.subtitle,.note{color:#596960}.warning{border-left:4px solid #ba691e;padding:14px;background:#fff}.charts{display:grid;grid-template-columns:1fr 1fr;gap:36px}.bar{display:grid;grid-template-columns:1.5fr 1fr 65px;gap:10px;align-items:center;margin:12px 0;font-size:13px}.track{background:#e5ebe7;height:12px}.track i{display:block;height:12px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:10px;text-align:left;border-bottom:1px solid #dbe2dd;white-space:nowrap}.scroll{overflow-x:auto}summary{cursor:pointer;padding:12px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}@media(max-width:700px){.charts{grid-template-columns:1fr}body{padding:0 14px}h1{font-size:26px}}'
    return '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>四类并发拓扑实测</title><style>'+css+'</style><body>'+header+table+'<section class="charts">'+charts+'</section>'+detail+evidence+'</body></html>'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    state = json.loads((args.root/'quick-matrix.json').read_text())
    from performance.targets.echomem.probes.concurrency_topology import _percentile
    for scene in state.get('scenes', []):
        sample_file = args.root/'samples'/f'{scene["name"]}-{scene["level"]}-samples.json'
        if sample_file.exists():
            samples = json.loads(sample_file.read_text())
            scene['healthy_p95_ms'] = _percentile([r['elapsed_ms'] for r in samples if r.get('operation') == 'search' and r.get('quality_ok')], .95)
    manifest_path = args.root/'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    diagnostic_path = args.root/'service-diagnostics.json'
    diagnostics = json.loads(diagnostic_path.read_text()) if diagnostic_path.exists() else None
    resource_file = args.root/'resources.json'
    resources = json.loads(resource_file.read_text()) if resource_file.exists() else None
    models_file = args.root/'public-models.json'
    models = json.loads(models_file.read_text()) if models_file.exists() else None
    (args.root/'report.html').write_text(render(state, manifest, diagnostics, resources, models))
