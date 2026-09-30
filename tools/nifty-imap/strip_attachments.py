#!/usr/bin/env python3
"""
@nifty の IMAP サーバ上のメールから、添付ファイルだけを削除する。

IMAP にはメールの一部を書き換える命令がないため、次の手順で置き換える:
  1. メールをダウンロードし、元のメールを --backup-dir に .eml で保存
  2. 添付ファイル部分を「削除しました」という短いテキストに置き換える
  3. 置き換えたメールを同じフォルダに APPEND（既読などのフラグと受信日時は引き継ぐ）
  4. 元のメールを削除

既定ではドライラン（対象の一覧を表示するだけ）。実際に処理するには --execute を付ける。

例:
    # 受信箱の 1MB 以上のメールで、2024-01-01 より前のものを確認
    python3 strip_attachments.py -u yourname@nifty.com --larger 1M --before 2024-01-01

    # 実際に添付ファイルを削除
    python3 strip_attachments.py -u yourname@nifty.com --larger 1M --before 2024-01-01 --execute

パスワードは環境変数 NIFTY_IMAP_PASSWORD、未設定なら対話入力で受け取る。
"""

import argparse
import base64
import datetime
import email
import email.generator
import email.policy
import getpass
import imaplib
import io
import os
import re
import sys

from delete_by_domain import (DEFAULT_HOST, DEFAULT_PORT, chunks, decode_header,
                              domain_matches, list_folders, quote_mailbox,
                              sender_domains)

FETCH_CHUNK = 200
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
POLICY = email.policy.compat32.clone(linesep="\r\n")


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
    # inline 画像など、ファイル名付きの非テキストパートも対象にする
    return part.get_filename() is not None and part.get_content_maintype() != "text"


def part_size(part):
    payload = part.get_payload(decode=True)
    return len(payload) if payload else len(str(part.get_payload()))


def strip_message(msg):
    """添付パートを置き換える。削除した (ファイル名, サイズ) のリストを返す。"""
    if not msg.is_multipart():
        return []  # 本文そのものが添付だけのメールは扱わない
    removed = []
    for part in msg.walk():
        if part is msg or not is_attachment(part):
            continue
        name = decode_header(part.get_filename() or "") or "(名前なし)"
        size = part_size(part)
        removed.append((name, size))
        note = "[添付ファイル削除: %s (%s, %s) %s]\n" % (
            name, part.get_content_type(), human(size),
            datetime.date.today().isoformat())
        for h in ("Content-Type", "Content-Transfer-Encoding",
                  "Content-Disposition", "Content-ID", "Content-Description",
                  "Content-Location"):
            del part[h]
        part["Content-Type"] = 'text/plain; charset="utf-8"'
        part["Content-Transfer-Encoding"] = "base64"
        part["Content-Disposition"] = "inline"
        part.set_payload(base64.encodebytes(note.encode("utf-8")).decode("ascii"))
    return removed


def to_bytes(msg):
    buf = io.BytesIO()
    email.generator.BytesGenerator(buf, mangle_from_=False, policy=POLICY).flatten(msg)
    return buf.getvalue()


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


def fetch_meta(imap, uids):
    """UID -> (flags, internaldate) を返す。"""
    meta = {}
    for chunk in chunks(uids, FETCH_CHUNK):
        typ, data = imap.uid("FETCH", b",".join(chunk).decode(),
                             "(UID FLAGS INTERNALDATE)")
        if typ != "OK":
            raise RuntimeError("FETCH failed: %r" % data)
        for line in data:
            if isinstance(line, tuple):
                line = line[0]
            if not line:
                continue
            m_uid = re.search(rb"UID (\d+)", line)
            m_date = re.search(rb'INTERNALDATE ("[^"]+")', line)
            if not m_uid:
                continue
            flags = [f.decode() for f in imaplib.ParseFlags(line)
                     if f.lower() != b"\\recent"]
            meta[m_uid.group(1)] = (flags, m_date.group(1).decode() if m_date else None)
    return meta


def fetch_body(imap, uid):
    typ, data = imap.uid("FETCH", uid.decode(), "(BODY.PEEK[])")
    if typ != "OK":
        raise RuntimeError("FETCH failed: %r" % data)
    for item in data:
        if isinstance(item, tuple):
            return item[1]
    return None


def safe_name(text):
    return re.sub(r'[\\/:*?"<>|&]', "_", text)


def main():
    p = argparse.ArgumentParser(
        description="@nifty IMAP のメールから添付ファイルだけを削除")
    p.add_argument("-u", "--user", required=True,
                   help="IMAP ユーザ名（@nifty のメールアドレス等）")
    p.add_argument("-f", "--folder", action="append",
                   help="対象フォルダ（複数指定可、既定: INBOX）")
    p.add_argument("--all-folders", action="store_true", help="全フォルダを対象にする")
    p.add_argument("-d", "--domain", action="append",
                   help="差出人ドメインで絞り込む（複数指定可）")
    p.add_argument("--larger", type=parse_size, default=parse_size("100K"),
                   help="このサイズより大きいメールだけ対象（例: 500K, 2M。既定 100K、0 で全件）")
    p.add_argument("--before", type=parse_date, metavar="YYYY-MM-DD",
                   help="この日付より前に受信したメールだけ対象")
    p.add_argument("--since", type=parse_date, metavar="YYYY-MM-DD",
                   help="この日付以降に受信したメールだけ対象")
    p.add_argument("--exact", action="store_true",
                   help="-d でサブドメインを対象外にする")
    p.add_argument("--backup-dir", default="attachment-backup",
                   help="元のメールを .eml で保存する場所（既定: ./attachment-backup）")
    p.add_argument("--no-backup", action="store_true",
                   help="元のメールを保存しない（添付は完全に失われます）")
    p.add_argument("--execute", action="store_true",
                   help="実際に処理する（指定しなければドライラン）")
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
            meta = fetch_meta(imap, uids) if args.execute else {}
            print("== %s: 候補 %d 件を確認中..." % (folder, len(uids)))
            done = []
            for uid in uids:
                raw = fetch_body(imap, uid)
                if raw is None:
                    continue
                msg = email.message_from_bytes(raw, policy=POLICY)
                from_h = decode_header(msg.get("From", ""))
                if targets and not any(
                        domain_matches(d, targets, not args.exact)
                        for d in sender_domains(from_h)):
                    continue
                removed = strip_message(msg)
                if not removed:
                    continue
                new_raw = to_bytes(msg)
                saved = len(raw) - len(new_raw)
                print("  [%s] %s | %s | %s" % (
                    uid.decode(), msg.get("Date", ""), from_h,
                    decode_header(msg.get("Subject", ""))))
                for name, size in removed:
                    print("        - %s (%s)" % (name, human(size)))
                total_msgs += 1
                total_bytes += max(saved, 0)
                if not args.execute:
                    continue

                if not args.no_backup:
                    d = os.path.join(args.backup_dir, safe_name(folder))
                    os.makedirs(d, exist_ok=True)
                    with open(os.path.join(d, "%s.eml" % uid.decode()), "wb") as fp:
                        fp.write(raw)
                flags, idate = meta.get(uid, ([], None))
                typ, data = imap.append(quote_mailbox(folder),
                                        "(%s)" % " ".join(flags) if flags else None,
                                        idate, new_raw)
                if typ != "OK":
                    print("   !! APPEND に失敗したため元のメールを残します: %r" % data,
                          file=sys.stderr)
                    continue
                typ, data = imap.uid("STORE", uid.decode(), "+FLAGS.SILENT", r"(\Deleted)")
                if typ != "OK":
                    print("   !! 元のメールの削除に失敗しました（重複して残ります）: %r" % data,
                          file=sys.stderr)
                    continue
                done.append(uid)
            if done:
                if "UIDPLUS" in caps:
                    for chunk in chunks(done, FETCH_CHUNK):
                        imap.uid("EXPUNGE", b",".join(chunk).decode())
                else:
                    imap.expunge()
                print("   -> %d 件の添付ファイルを削除しました" % len(done))
            imap.close()

        if args.execute:
            print("合計 %d 件、約 %s を削減しました。" % (total_msgs, human(total_bytes)))
            if not args.no_backup and total_msgs:
                print("元のメールは %s に保存しています。" % os.path.abspath(args.backup_dir))
        else:
            print("合計 %d 件、約 %s を削減できます（ドライラン）。"
                  "実行するには --execute を付けて再実行してください。"
                  % (total_msgs, human(total_bytes)))
    finally:
        try:
            imap.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
