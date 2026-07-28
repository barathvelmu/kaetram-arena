#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$repo_root/paper/tmlr"
build_dir="$repo_root/tmp/pdfs/tmlr-arxiv-build"
output_pdf="$repo_root/output/pdf/kaetram-tool-routing-arxiv.pdf"
log_file="$build_dir/arxiv.log"
pdf_file="$build_dir/arxiv.pdf"
pdf_info_file="$build_dir/arxiv.pdfinfo"

export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-946684800}"
export FORCE_SOURCE_DATE=1
export TZ=UTC

for command_name in latexmk pdfinfo; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "missing required command: $command_name" >&2
    exit 2
  fi
done

mkdir -p "$build_dir" "$(dirname "$output_pdf")"
(
  cd "$source_dir"
  latexmk \
    -gg \
    -pdf \
    -interaction=nonstopmode \
    -halt-on-error \
    -outdir="$build_dir" \
    arxiv.tex
)

if grep -Eq \
  'LaTeX Error|Citation .* undefined|There were undefined citations|There were undefined references|Overfull \\hbox|Overfull \\vbox' \
  "$log_file"; then
  echo "arXiv build contains a fatal citation, reference, or layout warning" >&2
  grep -En \
    'LaTeX Error|Citation .* undefined|There were undefined citations|There were undefined references|Overfull \\hbox|Overfull \\vbox' \
    "$log_file" >&2
  exit 1
fi

pdfinfo "$pdf_file" >"$pdf_info_file"
page_size="$(awk -F: '/^Page size/{sub(/^[[:space:]]+/, "", $2); print $2}' "$pdf_info_file")"
case "$page_size" in
  612*x*792*pts*\(letter\)) ;;
  *)
    echo "arXiv PDF is not US Letter: $page_size" >&2
    exit 1
    ;;
esac

cp "$pdf_file" "$output_pdf"
pages="$(awk -F: '/^Pages/{gsub(/[[:space:]]/, "", $2); print $2}' "$pdf_info_file")"
echo "built $output_pdf ($pages pages, US Letter, identified preprint)"
python3 "$repo_root/scripts/build_tmlr_arxiv_bundle.py"
