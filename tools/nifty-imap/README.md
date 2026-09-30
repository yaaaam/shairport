# @nifty IMAP 差出人ドメイン一括削除

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
