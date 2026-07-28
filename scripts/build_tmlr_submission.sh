#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$repo_root/paper/tmlr"
build_dir="$repo_root/tmp/pdfs/tmlr-build"
output_pdf="$repo_root/output/pdf/kaetram-tool-routing-tmlr-draft.pdf"
log_file="$build_dir/main.log"
pdf_file="$build_dir/main.pdf"
pdf_text_file="$build_dir/main.txt"
pdf_info_file="$build_dir/main.pdfinfo"
pdf_urls_file="$build_dir/main.urls"

# Freeze PDF timestamps to the sealed V2 experiment commit so clean builds are
# byte-reproducible instead of inheriting the builder's wall-clock time.
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1784851389}"
export FORCE_SOURCE_DATE=1
export TZ=UTC

for command_name in latexmk pdfinfo pdftotext python3; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "missing required command: $command_name" >&2
    exit 2
  fi
done

if [[ ! -f "$source_dir/main.tex" ]]; then
  echo "missing TMLR source: $source_dir/main.tex" >&2
  exit 2
fi

mkdir -p "$build_dir" "$(dirname "$output_pdf")"

(
  cd "$source_dir"
  latexmk \
    -gg \
    -pdf \
    -interaction=nonstopmode \
    -halt-on-error \
    -outdir="$build_dir" \
    main.tex
)

if grep -Eq \
  'LaTeX Error|Citation .* undefined|There were undefined citations|There were undefined references|Overfull \\hbox|Overfull \\vbox' \
  "$log_file"; then
  echo "TMLR build contains a fatal citation, reference, or layout warning" >&2
  grep -En \
    'LaTeX Error|Citation .* undefined|There were undefined citations|There were undefined references|Overfull \\hbox|Overfull \\vbox' \
    "$log_file" >&2
  exit 1
fi

pdfinfo "$pdf_file" >"$pdf_info_file"
pdfinfo -url "$pdf_file" >"$pdf_urls_file"
pdftotext "$pdf_file" "$pdf_text_file"
python3 "$repo_root/scripts/audit_submission_anonymity.py" \
  --source "$source_dir/main.tex" \
  --bibliography "$source_dir/references.bib" \
  --pdf-text "$pdf_text_file" \
  --pdf-info "$pdf_info_file" \
  --pdf-urls "$pdf_urls_file"

page_size="$(awk -F: '/^Page size/{sub(/^[[:space:]]+/, "", $2); print $2}' "$pdf_info_file")"
case "$page_size" in
  612*x*792*pts*\(letter\)) ;;
  *)
    echo "TMLR PDF is not US Letter: $page_size" >&2
    exit 1
    ;;
esac

cp "$pdf_file" "$output_pdf"
pages="$(awk -F: '/^Pages/{gsub(/[[:space:]]/, "", $2); print $2}' "$pdf_info_file")"
echo "built $output_pdf ($pages pages, US Letter)"
