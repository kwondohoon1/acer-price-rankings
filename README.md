# Acer Price Rankings CSV

Daily CSV generator for laptop ranking data from Coupang, Gmarket, and Naver.

The workflow runs every day at 08:00 Korea time and commits refreshed CSV files
under `data/`.

## Outputs

- `data/rankings_latest.csv`: all collected rows, up to 100 per market. Coupang's
  official product search API returns up to 10 ranked products.
- `data/foreign_rankings_latest.csv`: rows filtered to non-domestic laptop brands.
- `data/top5_latest.csv`: top 5 rows per market for frontend use.
- `data/YYYY-MM-DD/rankings.csv`: date-stamped archive.
- `data/YYYY-MM-DD/foreign_rankings.csv`: date-stamped foreign-brand archive.
- `data/YYYY-MM-DD/top5.csv`: date-stamped top 5 archive.
- `data/run_summary_latest.json`: collection counts and fallback notes.

The main CSV keeps the same 17 columns as the source workbook:

```text
날짜, 마켓, 순위, 판매처, 상품명(모델명), 제조사(브랜드), CPU, VGA, 메모리,
SSD, OS, 디스플레이, 정상가, 판매가, 혜택가, 쿠폰/카드혜택, URL
```

## GitHub Secrets

Required:

- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`
- `COUPANG_ACCESS_KEY`
- `COUPANG_SECRET_KEY`

Optional:

- repository variable `COUPANG_SUB_ID`: a registered Coupang Partners channel ID.

The Coupang collector uses the Partners product search API and preserves its
reported ranks. If the API is unavailable, Coupang rows are filled through the
Naver Shopping fallback and the fallback is recorded in `run_summary_latest.json`.
Gmarket direct collection can be blocked by Cloudflare; when that happens,
Gmarket rows use the same Naver Shopping fallback.

Coupang Partners product URLs are affiliate links. Any public page that displays
them must include the disclosure required by the Coupang Partners terms.

## Local Run

```bash
pip install -r requirements.txt
NAVER_CLIENT_ID=... NAVER_CLIENT_SECRET=... \
COUPANG_ACCESS_KEY=... COUPANG_SECRET_KEY=... \
python scripts/update_rankings.py
```

Useful environment variables:

- `SHOPPING_QUERY`: default `노트북`
- `MAX_ITEMS`: default `100`
- `GMARKET_CATEGORY_URL`: defaults to the requested Gmarket notebook category
- `COUPANG_SUB_ID`: optional registered Coupang Partners channel ID
