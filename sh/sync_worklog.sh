#!/usr/bin/env bash
#
# 프로젝트 폴더 바깥(상위 폴더)에 있는 학습 기록·작업 기록을 저장소 안으로 복사한다.
# GitHub에 올릴 문서를 '화이트리스트'로만 복사하므로, 목록에 없는 파일(다른 수강생 자료,
# 과제 2 문서, 안내 PDF, zip, .env 등)은 절대 들어가지 않는다.
#
# 사용:  bash sh/sync_worklog.sh   (프로젝트 루트 = all_total_vue_student 에서)
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=".."                      # 상위 폴더 (수업기록, 과제1_*.md 가 있는 곳)

mkdir -p work-log study-log

# ① 작업 기록 (과제 1) — 직접 이름을 적은 파일만
for f in 과제1_코드변경기록.md 과제1_개선항목_탐색.md 과제1_문제관찰기록.md \
         과제1_진행제안서.md 과제1_시트입력본.md 과제1_최종보고서_발표_정리본.md \
         과제1_마감전_TODO.md; do
    [ -f "$SRC/$f" ] && cp "$SRC/$f" "work-log/$f" && echo "복사: work-log/$f"
done

# ② 학습 자료
[ -f "$SRC/AI_미니프로젝트_마스터_설명서.md" ] && \
    cp "$SRC/AI_미니프로젝트_마스터_설명서.md" study-log/ && echo "복사: study-log/AI_미니프로젝트_마스터_설명서.md"
if [ -d "$SRC/수업기록" ]; then
    # PDF(안내 자료)와 .DS_Store는 제외하고, 파일을 지우지 않고 제자리에서 덮어쓴다.
    mkdir -p study-log/수업기록
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --inplace --exclude '.DS_Store' --exclude '*.pdf' "$SRC/수업기록/" study-log/수업기록/
    else
        (cd "$SRC/수업기록" && find . -type f ! -name '.DS_Store' ! -name '*.pdf' -print0) |
        while IFS= read -r -d '' f; do
            mkdir -p "study-log/수업기록/$(dirname "$f")"; cat "$SRC/수업기록/$f" > "study-log/수업기록/$f"
        done
    fi
    echo "복사: study-log/수업기록 (PDF 제외)"
fi

# ③ 안전 점검 — 비밀 값처럼 보이는 문자열이 있으면 중단한다 (값은 출력하지 않는다)
if grep -rIlE "lsv2_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}" work-log study-log final-report 2>/dev/null; then
    echo "⚠️ 위 파일에 키처럼 보이는 문자열이 있습니다. 지운 뒤 다시 실행하세요."; exit 1
fi
echo "완료: 비밀 문자열 검사 통과"
