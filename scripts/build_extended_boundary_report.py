"""Combine independent boundary observations without pretending they share a run."""
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

from performance.targets.echomem.orchestrator.report import write_objective_suite_html


def build(root):
    profiles, overview = [], []
    dispatched_cases = set()
    slow_search_sizes = set()
    for name in ('api', 'mcp', 'isolated-262144', 'isolated-524288', 'isolated-1048576'):
        folder = root / name
        source = folder / 'payload-boundary.json'
        if not source.exists():
            overview.append([name, 'NOT_RUN', '尚无本地持久化证据，未计为完成'])
            continue
        payload = json.loads(source.read_text())
        manifest_path = folder / 'manifest.json'
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        checks = payload.get('checks', [])
        detail = json.loads(checks[-1].get('detail') or '{}') if checks else {}
        counts = detail.get('outcome_counts', {})
        for row in detail.get('cases', []):
            if row.get('dispatched', True):
                dispatched_cases.add((row['content_bytes'], row['api'], row['encoding']))
            if row['api'] == 'search' and row['encoding'] == 'text' and row.get('dispatched', True) and row.get('http_status') is None:
                slow_search_sizes.add(row['content_bytes'])
        result = (f"计划{detail.get('cases_total')}项，发出{detail.get('cases_dispatched')}项；"
                  f"输入拒绝{counts.get('INPUT_REJECTED', 0)}，受理{counts.get('ACCEPTED_NOT_PERSISTENCE_PROOF', 0)}，"
                  f"服务错误{counts.get('SERVER_ERROR', 0)}，传输失败{counts.get('TRANSPORT_FAILED', 0)}，"
                  f"准备失败{counts.get('SETUP_FAILED', 0)}" if name != 'mcp' else
                  f"请求{detail.get('mcp_add_memory', {}).get('expected_chars')}字符，"
                  f"全文回读一致：{detail.get('mcp_add_memory', {}).get('persistence_verified')}")
        overview.append([name, payload.get('status'), result])
        profiles.append({'name': name, 'payload_boundary': payload, 'objectives': [{
            'id': name, 'name': 'API边界' if name != 'mcp' else 'MCP超长全文回读',
            'status': payload.get('status'), 'reason': result,
            'observed': {'deployment': manifest}, 'evidence': str(source),
            'owner': '测试平台/服务输入处理'}]})
    long_path = root / 'long' / 'result.json'
    if long_path.exists():
        data = json.loads(long_path.read_text())
        diagnosis_path = root / 'long' / 'diagnosis.json'
        diagnosis = json.loads(diagnosis_path.read_text()) if diagnosis_path.exists() else {}
        result = {key:data.get(key) for key in ('status', 'chars', 'chunk_chars', 'write_s',
                  'commit_to_terminal_s', 'terminal_state', 'recall_hits', 'recall_total')}
        overview.append(['长文本Commit', data.get('status'),
                         f"{data.get('chars')}字符；终态{data.get('terminal_state')}；"
                         f"提交至终态{data.get('commit_to_terminal_s', 0):.2f}秒；"
                         f"首中尾事实召回{data.get('recall_hits')}/{data.get('recall_total')}"])
        profiles.append({'name':'超长自然语言Commit（独立实测）', 'objectives':[{
            'id':'long-commit', 'name':'1MiB写入、Commit和召回', 'status':data.get('status'),
            'reason':'向量发布失败须与输入拒绝区分；不将供应商错误当作服务容量上限。',
            'observed':{'result':result, 'diagnosis':diagnosis}, 'evidence':str(long_path),
            'owner':'以真实阶段日志为准'}]})
    rule_path = root / 'rule-cost.json'
    if rule_path.exists():
        rule_data = json.loads(rule_path.read_text())
        samples = rule_data.get('samples', [])
        limited = [s for s in samples if s.get('status') == 'TIME_LIMIT']
        evidence = {key: rule_data.get(key) for key in (
            'scope', 'input', 'python', 'asset_sha256', 'pattern_timeout_s', 'rule_count')}
        evidence['timings'] = [{key: sample.get(key) for key in (
            'rule_index', 'chars', 'status', 'wall_ms', 'cpu_ms', 'matched')}
            for sample in samples]
        overview.append(['长Search逐规则CPU诊断', 'PARTIAL',
                         f'{len(samples)}个独立规则/长度样本，{len(limited)}个触及单规则计时上限；非HTTP性能数据'])
        profiles.append({'name': '长Search规则诊断（本机隔离执行）', 'objectives': [{
            'id': 'rule-cost', 'name': '路由正则长输入耗时', 'status': 'PARTIAL',
            'reason': '对运行镜像同哈希规则资产逐条执行真实Python正则，输入为重复ASCII x。'
                      '部分规则的未锚定负向前瞻可能反复扫描后缀，局部计时支持超线性计算风险。'
                      'TIME_LIMIT是诊断器主动中断，不是HTTP超时；wall_ms与cpu_ms不代表服务器P95。'
                      '新增逐规则开始/结束打点后，仍需在独立服务实例按trace关联HTTP请求，'
                      '才能确认原长Search阻塞位置。此问题与短Search的semantic/画像匹配涨幅分开分析。',
            'observed': evidence, 'evidence': str(rule_path), 'owner': 'EchoMem路由规则；待HTTP打点复测'}]})
    return {'title':'超长写入与API边界 · 补测报告',
            'created_at':datetime.now(timezone.utc).isoformat(),
            'model_evidence_note':'MCP全文回读只验证会话历史保存，不要求模型抽取。长Commit有真实阶段日志及Embedding 429证据；不将接口200或未启用mock当作成功模型调用证明。',
            'scope':'42项离散API边界、1MiB MCP全文回读、独立自然语言长Commit。不是M1-M3完整验收报告。',
            'summary':f'跨连续及独立实例，实际发出的不同API/长度/编码组合共{len(dispatched_cases)}/42项。'
                      f'文本Search在以下字节长度发生传输失败/超时：{sorted(slow_search_sizes)}。'
                      '发出不等于通过；独立补测不覆盖原连续实例阻塞记录。各实验配置指纹与来源分别保留。',
            'overview_title':'边界与超长写入结果总览',
            'method':'API：7个长度档×Message/Commit/Search×文本/二进制。Commit的原始文本请求测试协议边界，真正长Commit先写入Session再提交。MCP使用add_memory写入1MiB字符，再用同一租户history读取全文逐字对账；它不触发或证明模型抽取完成。长Commit使用独立自然语言事实，在首段、中段、尾段检查召回，保留模型依赖失败。',
            'overview':{'headers':['场景','状态','证据'], 'rows':overview}, 'profiles':profiles}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.root)
    (args.root/'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    write_objective_suite_html(result, args.root/'report.html')
