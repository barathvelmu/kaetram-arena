#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$repo_root/reference"
build_dir="$repo_root/tmp/pdfs/submission-build"
output_pdf="$repo_root/output/pdf/kaetram-opd-naacl-working-draft.pdf"
log_file="$build_dir/naacl_submission.log"
aux_file="$build_dir/naacl_submission.aux"

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
    -pdf \
    -interaction=nonstopmode \
    -halt-on-error \
    -outdir="$build_dir" \
    naacl_submission.tex
)

if grep -Eq \
  'LaTeX Error|Citation .* undefined|There were undefined citations|There were undefined references|Overfull \\hbox|Overfull \\vbox' \
  "$log_file"; then
  echo "submission build contains a fatal citation, reference, or layout warning" >&2
  grep -En \
    'LaTeX Error|Citation .* undefined|There were undefined citations|There were undefined references|Overfull \\hbox|Overfull \\vbox' \
    "$log_file" >&2
  exit 1
fi

page_size="$(pdfinfo "$build_dir/naacl_submission.pdf" | awk -F: '/^Page size/{sub(/^[[:space:]]+/, "", $2); print $2}')"
case "$page_size" in
  595.276*x*841.89*pts*\(A4\)) ;;
  *)
    echo "submission PDF is not A4: $page_size" >&2
    exit 1
    ;;
esac

main_content_page="$(
  sed -n \
    's/.*newlabel{acl:main-content-end}{{[^}]*}{\([0-9][0-9]*\)}.*/\1/p' \
    "$aux_file" | tail -n 1
)"
if [[ -z "$main_content_page" ]]; then
  echo "could not locate the main-content page sentinel" >&2
  exit 1
fi
if (( main_content_page > 8 )); then
  echo "main content exceeds the ACL long-paper limit: ends on page $main_content_page" >&2
  exit 1
fi

cp "$build_dir/naacl_submission.pdf" "$output_pdf"
echo "built $output_pdf (main content ends on page $main_content_page)"
