#!/usr/bin/env python3
"""
@nifty の IMAP サーバから、添付ファイル付きのメールを PC にバックアップ（.eml）してから
サーバ上の元のメールを削除する。

保存した .eml ファイルは Thunderbird や Outlook などのメールソフトでそのまま開ける。
保存に成功したメールだけを削除する。

既定ではドライラン（対象の一覧を表示するだけ）。実際に処理するには --execute を付ける。

例:
    # 受信箱の添付ファイル付きメールを確認
    python3 backup_delete_attachments.py -u yourname@nifty.com

    # 2024-01-01 より前のものを保存して削除
    python3 backup_delete_attachments.py -u yourname@nifty.com --before 2024-01-01 --execute

パスワードは環境変数 NIFTY_IMAP_PASSWORD、未設定なら対話入力で受け取る。
"""

import argparse
import datetime
import email
import email.policy
import email.utils
import getpass
import imaplib
import os
import re
import sys

from delete_by_domain import (DEFAULT_HOST, DEFAULT_PORT, chunks, decode_header,
                              domain_matches, list_folders, quote_mailbox,
                              sender_domains)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
EXPUNGE_CHUNK = 200


def parse_size(text):
    m = re.fullmatch(r"(\d+)\s*([kKmMgG]?)[bB]?", text.strip())
    if not m:
        raise argparse.ArgumentTypeError("サイズの形式が不正です: %s" % text)
    n = int(m.group(1))
    return n * {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2).lower()]


def parse_date(text):
    try:
        d = datetime.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("日付は YYYY-MM-DD で指定してください: %s" % text)
    return "%d-%s-%d" % (d.day, MONTHS[d.month - 1], d.year)


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d%s" if unit == "B" else "%.1f%s") % (n, unit)
        n /= 1024.0


def is_attachment(part):
    if part.is_multipart():
        return False
    disp = (part.get("Content-Disposition") or "").split(";")[0].strip().lower()
    if disp == "attachment":
        return True
    # inline 画像など、ファイル名付きの非テキストパートも添付とみなす
    return part.get_filename() is not None and part.get_content_maintype() != "text"


def attachment_names(msg):
    return [decode_header(p.get_filename() or "") or "(名前なし)"
            for p in msg.walk() if is_attachment(p)]


def search(imap, args):
    criteria = []
    if args.larger:
        criteria += ["LARGER", str(args.larger)]
    if args.before:
        criteria += ["BEFORE", args.before]
    if args.since:
        criteria += ["SINCE", args.since]
    uids = set()
    for dom in args.domain or [None]:
        c = criteria + (["FROM", quote_mailbox(dom)] if dom else [])
        typ, data = imap.uid("SEARCH", None, *(c or ["ALL"]))
        if typ != "OK":
            raise RuntimeError("SEARCH failed: %r" % data)
        uids.update(data[0].split())
    return sorted(uids, key=int)


def fetch_body(imap, uid):
    typ, data = imap.uid("FETCH", uid.decode(), "(BODY.PEEK[])")
    if typ != "OK":
        raise RuntimeError("FETCH failed: %r" % data)
    for item in data:
        if isinstance(item, tuple):
            return item[1]
    return None


def safe_name(text, limit=60):
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", text).strip(" .")
    return text[:limit] or "no-subject"


def backup_path(base, folder, msg, uid):
    try:
        dt = email.utils.parsedate_to_datetime(msg.get("Date", ""))
        stamp = dt.strftime("%Y%m%d-%H%M%S")
    except (TypeError, ValueError):
        stamp = "nodate"
    d = os.path.join(base, safe_name(folder, 100))
    os.makedirs(d, exist_ok=True)
    name = "%s_%s" % (stamp, safe_name(decode_header(msg.get("Subject", ""))))
    path = os.path.join(d, name + ".eml")
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(d, "%s_%d.eml" % (name, n))
    return path


def write_backup(path, raw):
    with open(path, "wb") as fp:
        fp.write(raw)
        fp.flush()
        os.fsync(fp.fileno())
    if os.path.getsize(path) != len(raw):
        raise IOError("保存したファイルのサイズが一致しません: %s" % path)


def main():
    p = argparse.ArgumentParser(
        description="@nifty IMAP の添付ファイル付きメールを PC に保存してサーバから削除")
    p.add_argument("-u", "--user", required=True,
                   help="IMAP ユーザ名（@nifty のメールアドレス等）")
    p.add_argument("-f", "--folder", action="append",
                   help="対象フォルダ（複数指定可、既定: INBOX）")
    p.add_argument("--all-folders", action="store_true", help="全フォルダを対象にする")
    p.add_argument("-d", "--domain", action="append",
                   help="差出人ドメインで絞り込む（複数指定可）")
    p.add_argument("--exact", action="store_true",
                   help="-d でサブドメインを対象外にする")
    p.add_argument("--larger", type=parse_size, default=0,
                   help="このサイズより大きいメールだけ対象（例: 500K, 2M）")
    p.add_argument("--before", type=parse_date, metavar="YYYY-MM-DD",
                   help="この日付より前に受信したメールだけ対象")
    p.add_argument("--since", type=parse_date, metavar="YYYY-MM-DD",
                   help="この日付以降に受信したメールだけ対象")
    p.add_argument("-o", "--backup-dir", default="nifty-backup",
                   help="保存先フォルダ（既定: ./nifty-backup）")
    p.add_argument("--execute", action="store_true",
                   help="実際に保存・削除する（指定しなければドライラン）")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = p.parse_args()

    targets = [d.lower().lstrip("@").strip() for d in args.domain or []]
    password = os.environ.get("NIFTY_IMAP_PASSWORD") or getpass.getpass(
        "IMAP password for %s: " % args.user)

    imap = imaplib.IMAP4_SSL(args.host, args.port)
    try:
        imap.login(args.user, password)
    except imaplib.IMAP4.error as e:
        sys.exit("ログインに失敗しました: %s" % e)

    caps = {c.decode().upper() if isinstance(c, bytes) else c.upper()
            for c in imap.capabilities}
    total_msgs = total_bytes = 0
    try:
        folders = list_folders(imap) if args.all_folders else (args.folder or ["INBOX"])
        for folder in folders:
            typ, data = imap.select(quote_mailbox(folder), readonly=not args.execute)
            if typ != "OK":
                print("[skip] %s を開けません: %r" % (folder, data), file=sys.stderr)
                continue
            uids = search(imap, args)
            print("== %s: 候補 %d 件を確認中..." % (folder, len(uids)))
            done = []
            try:
                for uid in uids:
                    raw = fetch_body(imap, uid)
                    if raw is None:
                        continue
                    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
                    from_h = decode_header(msg.get("From", ""))
                    if targets and not any(
                            domain_matches(d, targets, not args.exact)
                            for d in sender_domains(from_h)):
                        continue
                    names = attachment_names(msg)
                    if not names:
                        continue
                    print("  [%s] %s | %s | %s (%s)" % (
                        uid.decode(), msg.get("Date", ""), from_h,
                        decode_header(msg.get("Subject", "")), human(len(raw))))
                    print("        添付: %s" % ", ".join(names))
                    total_msgs += 1
                    total_bytes += len(raw)
                    if not args.execute:
                        continue

                    path = backup_path(args.backup_dir, folder, msg, uid)
                    try:
                        write_backup(path, raw)
                    except (IOError, OSError) as e:
                        print("   !! 保存に失敗したためサーバのメールを残します: %s" % e,
                              file=sys.stderr)
                        continue
                    typ, data = imap.uid("STORE", uid.decode(), "+FLAGS.SILENT",
                                         r"(\Deleted)")
                    if typ != "OK":
                        print("   !! 削除に失敗しました: %r" % data, file=sys.stderr)
                        continue
                    print("        -> %s" % path)
                    done.append(uid)
            finally:
                # 途中でエラーになっても、保存済みのメールは確実に削除を確定する
                if done:
                    if "UIDPLUS" in caps:
                        for chunk in chunks(done, EXPUNGE_CHUNK):
                            imap.uid("EXPUNGE", b",".join(chunk).decode())
                    else:
                        imap.expunge()
                    print("   -> %d 件を保存して削除しました" % len(done))
            imap.close()

        if args.execute:
            print("合計 %d 件（%s）を保存して削除しました。保存先: %s"
                  % (total_msgs, human(total_bytes), os.path.abspath(args.backup_dir)))
        else:
            print("合計 %d 件（%s）が対象です（ドライラン）。"
                  "実行するには --execute を付けて再実行してください。"
                  % (total_msgs, human(total_bytes)))
    finally:
        try:
            imap.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
