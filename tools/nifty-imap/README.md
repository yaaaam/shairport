# @nifty IMAP メール整理スクリプト

- `delete_by_domain.py` — 差出人ドメインでメールを一括削除
- `strip_attachments.py` — メールを残したまま添付ファイルだけを削除

## 差出人ドメインで一括削除 (`delete_by_domain.py`)

`delete_by_domain.py` は @nifty の IMAP サーバ (`imap.nifty.com:993`, SSL) に接続し、
指定したドメイン（例: `m3.com`）から届いたメールを一括削除します。Python 3 標準ライブラリのみで動作します。

## 使い方

```sh
# 1. まずドライランで対象を確認（削除はしない）
python3 delete_by_domain.py -u yourname@nifty.com -d m3.com

# 2. 問題なければ削除
python3 delete_by_domain.py -u yourname@nifty.com -d m3.com --execute
```

パスワードは環境変数 `NIFTY_IMAP_PASSWORD` から読み、なければ入力を求めます。

| オプション | 説明 |
|---|---|
| `-d DOMAIN` | 対象ドメイン。複数回指定可 |
| `-f FOLDER` | 対象フォルダ（既定 `INBOX`）。複数回指定可 |
| `--all-folders` | 全フォルダを対象にする |
| `--exact` | サブドメイン（`mail.m3.com` など）を対象外にする |
| `--trash FOLDER` | 完全削除せず指定フォルダへ移動する |
| `--execute` | 実際に削除／移動する（付けなければドライラン） |
| `-q` | メールごとの一覧を表示しない |

## 判定方法

サーバ側の `SEARCH FROM` で候補を絞ったあと、各メールの `From` ヘッダのアドレスを解析し、
`@` 以降のドメインが指定ドメインと一致（既定ではサブドメインも含む）するものだけを削除します。
`notm3.com` や、表示名に `m3.com` を含むだけのメールは対象になりません。

## 注意

- `--trash` なしの `--execute` は完全削除で、元に戻せません。
- 日本語フォルダ名は IMAP の修正 UTF-7 表記で指定します（`--all-folders` の出力で確認できます）。
- IMAP のユーザ名は @nifty の設定によって異なります（メールアドレスや @nifty ID など）。
  メールソフトの IMAP 設定と同じものを使ってください。

## 添付ファイルだけを削除 (`strip_attachments.py`)

IMAP にはメールの一部を書き換える命令がないため、メールをダウンロードして添付ファイル部分を
`[添付ファイル削除: 資料.pdf (application/pdf, 293.0KB) 2026-09-30]` という短いテキストに置き換え、
同じフォルダへ書き戻してから元のメールを削除します。既読・フラグ・受信日時は引き継ぎます。
元のメールは既定で `./attachment-backup/フォルダ名/UID.eml` に保存します。

```sh
# 1MB 以上・2024年より前のメールを確認（ドライラン）
python3 strip_attachments.py -u yourname@nifty.com --larger 1M --before 2024-01-01

# 実行
python3 strip_attachments.py -u yourname@nifty.com --larger 1M --before 2024-01-01 --execute
```

`delete_by_domain.py` と同じフォルダに置いて実行してください（共通処理を読み込みます）。

| オプション | 説明 |
|---|---|
| `--larger SIZE` | このサイズより大きいメールだけ対象（既定 `100K`、`0` で全件） |
| `--before` / `--since YYYY-MM-DD` | 受信日で絞り込む |
| `-d DOMAIN` | 差出人ドメインで絞り込む |
| `-f FOLDER` / `--all-folders` | 対象フォルダ |
| `--backup-dir DIR` | 元のメールの保存先（既定 `./attachment-backup`） |
| `--no-backup` | 元のメールを保存しない |
| `--execute` | 実際に処理する |

注意:
- 処理後のメールは新しいメールとしてサーバに登録されるため、UID が変わります。
- `Content-Disposition: attachment` のパートと、ファイル名付きの画像などテキスト以外のパートを添付とみなします。
  本文中に埋め込まれた画像も削除されます。
- 電子署名（S/MIME・DKIM など）付きメールは、署名が無効になります。
- ドライランでも判定のため候補メールをダウンロードするので、`--larger` で絞ると速くなります。
