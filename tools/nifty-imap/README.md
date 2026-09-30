# @nifty IMAP メール整理スクリプト

- `delete_by_domain.py` — 差出人ドメインでメールを一括削除
- `backup_delete_attachments.py` — 添付ファイル付きメールを PC に保存してサーバから削除

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

## 添付ファイル付きメールを保存して削除 (`backup_delete_attachments.py`)

添付ファイル付きのメールを丸ごと `.eml` ファイルとして PC に保存し、保存に成功したものだけを
サーバから削除します。`.eml` は Thunderbird・Outlook などのメールソフトでそのまま開けます（添付ファイルも含まれます）。

保存先は `./nifty-backup/フォルダ名/受信日時_件名.eml` です。

```sh
# 対象を確認（ドライラン）
python3 backup_delete_attachments.py -u yourname@nifty.com

# 2024年より前のものを保存して削除
python3 backup_delete_attachments.py -u yourname@nifty.com --before 2024-01-01 --execute
```

`delete_by_domain.py` と同じフォルダに置いて実行してください（共通処理を読み込みます）。

| オプション | 説明 |
|---|---|
| `-o DIR` | 保存先フォルダ（既定 `./nifty-backup`） |
| `--before` / `--since YYYY-MM-DD` | 受信日で絞り込む |
| `--larger SIZE` | このサイズより大きいメールだけ対象（例: `500K`, `2M`） |
| `-d DOMAIN` | 差出人ドメインで絞り込む |
| `-f FOLDER` / `--all-folders` | 対象フォルダ（既定 `INBOX`） |
| `--execute` | 実際に保存・削除する |

注意:
- `Content-Disposition: attachment` のパートと、ファイル名付きの画像などテキスト以外のパートを添付とみなします。
  本文に画像が埋め込まれた HTML メール（メルマガなど）も対象になるので、ドライランで確認してください。
- ドライランでも判定のためメールをダウンロードします。件数が多い場合は `--before` や `--larger` で絞ると速くなります。
- 保存先のバックアップは大切に保管してください。サーバからは削除されます。
