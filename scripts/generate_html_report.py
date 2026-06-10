#!/usr/bin/env python3
"""Generate HTML report from OpenViking Memory QA results."""

import argparse
import csv
import json
from pathlib import Path
from datetime import datetime


def load_results(csv_path: str) -> list[dict]:
    """Load results from CSV file."""
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return list(reader)


def evaluate_answer(question: str, answer: str, response: str) -> tuple[str, float]:
    """Manually evaluate answer quality."""
    answer_lower = answer.lower().strip()
    response_lower = response.lower().strip()

    # Check if unknown
    if 'unknown' in response_lower and answer_lower not in response_lower:
        return 'wrong', 0.0

    # Check if answer is in response
    if answer_lower in response_lower or response_lower in answer_lower:
        return 'correct', 1.0

    # Needs manual check
    return 'check', 0.5


def evaluate_row(row: dict) -> tuple[str, float]:
    """Prefer model-judge verdicts when present; fall back to lexical checks."""
    verdict = str(row.get("result") or "").strip().upper()
    if verdict == "CORRECT":
        return "correct", 1.0
    if verdict == "WRONG":
        return "wrong", 0.0
    return evaluate_answer(row["question"], row["answer"], row["response"])


def generate_html_report(results: list[dict], output_path: str, run_name: str = "OpenViking Memory QA"):
    """Generate HTML report."""

    # Evaluate all results
    evaluations = []
    total_score = 0
    for row in results:
        status, score = evaluate_row(row)
        evaluations.append({'status': status, 'score': score})
        total_score += score

    accuracy = (total_score / len(results) * 100) if results else 0

    # Calculate statistics
    correct_count = sum(1 for e in evaluations if e['status'] == 'correct')
    wrong_count = sum(1 for e in evaluations if e['status'] == 'wrong')
    check_count = sum(1 for e in evaluations if e['status'] == 'check')

    # Calculate timing stats
    time_costs = [float(r['time_cost']) for r in results if r.get('time_cost')]
    total_time = sum(time_costs)
    avg_time = total_time / len(time_costs) if time_costs else 0

    # Generate HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{run_name} - 评测报告</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}

        h1 {{
            color: #2c3e50;
            margin-bottom: 10px;
            font-size: 32px;
        }}

        .subtitle {{
            color: #7f8c8d;
            margin-bottom: 30px;
            font-size: 14px;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }}

        .stat-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
        }}

        .stat-card.success {{
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
        }}

        .stat-card.warning {{
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
        }}

        .stat-card.info {{
            background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
        }}

        .stat-value {{
            font-size: 36px;
            font-weight: bold;
            margin-bottom: 5px;
        }}

        .stat-label {{
            font-size: 14px;
            opacity: 0.9;
        }}

        .question-list {{
            margin-top: 40px;
        }}

        .question-item {{
            background: #f8f9fa;
            border-left: 4px solid #ddd;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 4px;
        }}

        .question-item.correct {{
            border-left-color: #38ef7d;
            background: #f0fdf4;
        }}

        .question-item.wrong {{
            border-left-color: #f5576c;
            background: #fef2f2;
        }}

        .question-item.check {{
            border-left-color: #fbbf24;
            background: #fffbeb;
        }}

        .question-header {{
            display: flex;
            align-items: center;
            margin-bottom: 15px;
        }}

        .question-number {{
            font-weight: bold;
            font-size: 18px;
            margin-right: 10px;
            color: #2c3e50;
        }}

        .question-status {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
            margin-left: auto;
        }}

        .question-status.correct {{
            background: #38ef7d;
            color: white;
        }}

        .question-status.wrong {{
            background: #f5576c;
            color: white;
        }}

        .question-status.check {{
            background: #fbbf24;
            color: white;
        }}

        .question-text {{
            font-size: 16px;
            font-weight: 500;
            color: #2c3e50;
            margin-bottom: 15px;
        }}

        .answer-section {{
            display: grid;
            gap: 10px;
        }}

        .answer-row {{
            display: flex;
            gap: 10px;
        }}

        .answer-label {{
            font-weight: 600;
            color: #64748b;
            min-width: 80px;
        }}

        .answer-value {{
            flex: 1;
            color: #334155;
        }}

        .answer-value.response {{
            color: #0ea5e9;
        }}

        .meta-info {{
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid #e2e8f0;
            font-size: 12px;
            color: #94a3b8;
        }}

        .filter-buttons {{
            margin-bottom: 20px;
            display: flex;
            gap: 10px;
        }}

        .filter-btn {{
            padding: 8px 16px;
            border: 2px solid #e2e8f0;
            background: white;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.2s;
        }}

        .filter-btn:hover {{
            background: #f8fafc;
        }}

        .filter-btn.active {{
            background: #667eea;
            color: white;
            border-color: #667eea;
        }}

        .progress-bar {{
            width: 100%;
            height: 30px;
            background: #e2e8f0;
            border-radius: 15px;
            overflow: hidden;
            margin: 20px 0;
        }}

        .progress-fill {{
            height: 100%;
            background: linear-gradient(90deg, #11998e 0%, #38ef7d 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 14px;
            transition: width 0.3s ease;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 {run_name}</h1>
        <div class="subtitle">生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>

        <div class="progress-bar">
            <div class="progress-fill" style="width: {accuracy:.1f}%">
                准确率: {accuracy:.1f}%
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card success">
                <div class="stat-value">{correct_count}</div>
                <div class="stat-label">✅ 完全正确</div>
            </div>
            <div class="stat-card warning">
                <div class="stat-label">❌ 错误</div>
                <div class="stat-value">{wrong_count}</div>
            </div>
            <div class="stat-card info">
                <div class="stat-value">{check_count}</div>
                <div class="stat-label">⚠️ 需要检查</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{len(results)}</div>
                <div class="stat-label">📝 总题数</div>
            </div>
            <div class="stat-card info">
                <div class="stat-value">{total_time/60:.1f}m</div>
                <div class="stat-label">⏱️ 总耗时</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{avg_time:.1f}s</div>
                <div class="stat-label">📈 平均耗时</div>
            </div>
        </div>

        <div class="filter-buttons">
            <button class="filter-btn active" onclick="filterQuestions('all')">全部 ({len(results)})</button>
            <button class="filter-btn" onclick="filterQuestions('correct')">✅ 正确 ({correct_count})</button>
            <button class="filter-btn" onclick="filterQuestions('wrong')">❌ 错误 ({wrong_count})</button>
            <button class="filter-btn" onclick="filterQuestions('check')">⚠️ 需检查 ({check_count})</button>
        </div>

        <div class="question-list">
"""

    # Add each question
    for i, (row, eval_data) in enumerate(zip(results, evaluations), 1):
        status = eval_data['status']
        status_icon = {'correct': '✅', 'wrong': '❌', 'check': '⚠️'}[status]
        status_text = {'correct': '正确', 'wrong': '错误', 'check': '需检查'}[status]

        retrieval_count = row.get('retrieval_count', 'N/A')
        time_cost = float(row.get('time_cost', 0))

        html += f"""
            <div class="question-item {status}" data-status="{status}">
                <div class="question-header">
                    <span class="question-number">{status_icon} Q{i}</span>
                    <span class="question-status {status}">{status_text}</span>
                </div>
                <div class="question-text">{row['question']}</div>
                <div class="answer-section">
                    <div class="answer-row">
                        <span class="answer-label">标准答案:</span>
                        <span class="answer-value">{row['answer']}</span>
                    </div>
                    <div class="answer-row">
                        <span class="answer-label">模型回答:</span>
                        <span class="answer-value response">{row['response']}</span>
                    </div>
                </div>
                <div class="meta-info">
                    检索数: {retrieval_count} | 耗时: {time_cost:.2f}s | 问题ID: {row.get('question_id', 'N/A')}
                </div>
            </div>
"""

    html += """
        </div>
    </div>

    <script>
        function filterQuestions(status) {
            const items = document.querySelectorAll('.question-item');
            const buttons = document.querySelectorAll('.filter-btn');

            // Update button states
            buttons.forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');

            // Filter items
            items.forEach(item => {
                if (status === 'all' || item.dataset.status === status) {
                    item.style.display = 'block';
                } else {
                    item.style.display = 'none';
                }
            });
        }
    </script>
</body>
</html>
"""

    # Write HTML file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✅ HTML 报告已生成: {output_path}")
    print(f"📊 准确率: {accuracy:.1f}% ({total_score}/{len(results)})")


def main():
    parser = argparse.ArgumentParser(description="Generate HTML report from QA results")
    parser.add_argument("csv_path", help="Path to CSV results file")
    parser.add_argument("--output", "-o", help="Output HTML file path", default=None)
    parser.add_argument("--name", "-n", help="Report name", default="OpenViking Memory QA 评测报告")

    args = parser.parse_args()

    # Load results
    results = load_results(args.csv_path)

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        csv_path = Path(args.csv_path)
        output_path = csv_path.parent / f"{csv_path.stem}_report.html"

    # Generate report
    generate_html_report(results, str(output_path), args.name)


if __name__ == "__main__":
    main()
