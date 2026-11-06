"""관제 화면에서 저장한 로그 JSON을 분석용 CSV로 변환합니다 (표준 라이브러리만 사용)."""
import argparse
import csv
import json
from datetime import datetime
from pathlib import Path


def cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value  # 스프레드시트에서 문자열을 수식으로 실행하지 않음
    return value


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: cell(row.get(key)) for key in columns})


def convert(source, output_root):
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    logs = data.get("logs") if isinstance(data, dict) else data
    if not isinstance(logs, list):
        raise ValueError("로그 JSON이 아닙니다. 관제 화면의 '조회한 로그 JSON 저장'으로 저장하세요.")
    for index, log in enumerate(logs, 1):
        if not isinstance(log, dict) or "action" not in log:
            raise ValueError(f"{index}번째 항목에 로그 action이 없습니다.")
        if log.get("features") is not None and not isinstance(log["features"], dict):
            raise ValueError(f"{index}번째 항목의 features가 객체가 아닙니다.")

    # 원본 파일의 행 번호로 연결: ID가 없는 행도 유실하지 않음.
    metadata = ["source_row", "id", "timestamp", "user_id", "flight_id", "reservation_id", "action"]
    outcomes = ["decision", "rule_score", "ai_score", "risk_score", "is_anomaly", "reasons"]
    feature_names = sorted({key for log in logs for key in (log.get("features") or {})})
    all_rows, feature_rows, label_rows = [], [], []
    for index, log in enumerate(logs, 1):
        features = log.get("features") or {}
        base = {key: log.get(key) for key in metadata}
        base["source_row"] = index
        flat = {"feature__" + key: value for key, value in features.items()}
        all_rows.append({**base, **{key: log.get(key) for key in outcomes}, **flat})
        if features:
            feature_rows.append({**base, **flat})
            label_rows.append({"source_row": index, "id": log.get("id")})

    target = output_root / datetime.now().strftime("log_export_%Y%m%d_%H%M%S_%f")
    target.mkdir(parents=True, exist_ok=False)
    feature_columns = ["feature__" + key for key in feature_names]
    write_csv(target / "all_events.csv", metadata + outcomes + feature_columns, all_rows)
    write_csv(target / "feature_events.csv", metadata + feature_columns, feature_rows)
    write_csv(target / "labels_to_fill.csv", ["source_row", "id", "experiment_id", "scenario", "label", "label_basis"], label_rows)
    summary = {
        "source_file": source.name,
        "export_filter": data.get("filter") if isinstance(data, dict) else None,
        "fetched_at": data.get("fetched_at") if isinstance(data, dict) else None,
        "event_count": len(logs),
        "events_with_features": len(feature_rows),
        "events_without_features": len(logs) - len(feature_rows),
        "notes": [
            "입력 JSON에 포함된 로그만 변환하며, 전체 DB 로그가 아닐 수 있습니다. 중복 제거는 하지 않습니다.",
            "빈 칸은 결측치이며 0과 다릅니다. features가 없는 행은 학습 후보에서 제외했습니다.",
            "labels_to_fill.csv의 label은 normal 또는 abuse를 실험 설계 근거로 기입하세요. 불명확하면 빈 칸을 유지하세요.",
            "decision, risk_score, is_anomaly 등으로 정답을 자동 생성하지 않았습니다.",
            "feature_events.csv의 ID·시간·행 번호는 연결/분할용이며 그대로 모델 입력에 넣지 마세요.",
            "동일 요청의 여러 이벤트가 있으면 학습 전 요청 단위 중복 여부를 확인하세요.",
            "원본 IP·기기 ID·user_agent는 CSV에 포함하지 않습니다. 원본 JSON은 별도로 보관하세요.",
            "이 출력은 학습 완료 데이터나 실제 탐지 성능 평가 결과가 아닙니다."
        ],
    }
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return target, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", nargs="?", default="alarm-flight-logs.json")
    parser.add_argument("--output-dir", default="experiments")
    args = parser.parse_args()
    try:
        target, summary = convert(Path(args.json_file).expanduser(), Path(args.output_dir).expanduser())
    except (OSError, ValueError) as error:
        parser.exit(1, f"변환 실패: {error}\n")
    print(f"변환 완료: 전체 로그 {summary['event_count']}개 / 행동 정보가 있는 로그 {summary['events_with_features']}개")
    print(f"저장 위치: {target.resolve()}")
    print("all_events.csv / feature_events.csv / labels_to_fill.csv / summary.json")
    print("정상·남용 정답은 자동으로 붙이지 않았습니다. 현재 조회한 로그 범위만 변환했습니다.")


if __name__ == "__main__":
    main()
