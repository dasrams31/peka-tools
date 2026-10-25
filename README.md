# Peka Tools — tools.peka.ai.id

Katalog tools open source pilihan Peka: repo dan skill yang pernah tayang
di SPILL Repo Peka, edisi khusus Hallmark, dan tools open source buatan
Peka sendiri. Desain: layout direktori ala appverse.id, warna ala Peka
(hitam #111, kuning #ffd60a).

## Desain

Warna Peka (hitam #111, kuning #ffd60a, krem #f5f1e6) + layout direktori
ala appverse.id. Poles 26 Okt 2026 pakai referensi skill
[anti-slop](https://github.com/miqdadbadjuber/anti-slop) (salinan referensi di
`~/workspace/anti-slop-ref/`): teks dim naik kontras ke 5,48:1 (lolos WCAG AA,
sebelumnya 3,51:1), radius dibedakan (pill hanya untuk chip/nav, tag dan
tombol jadi kotak membulat kecil), focus ring kuning untuk navigasi keyboard,
target sentuh chip/nav minimal 44px, state loading + error saat katalog gagal
dimuat, tanpa em-dash di copy, tanpa CTA generik, angka statistik dihitung
dari data (bukan klaim).

## Files

- `index.html` — satu halaman: daftar + detail + koleksi + arsip + disimpan
  + usulkan + tentang. Vanilla JS, tanpa build, tanpa dependensi eksternal.
- `tools.json` — seluruh data katalog (tools, koleksi, arsip, kategori).
  Angka bintang/fork/bahasa/lisensi dari GitHub API saat data disusun
  (`generated`).
- `serve.py` — server statis stdlib (127.0.0.1:8094) + endpoint
  `POST /api/usulan` (validasi + rate limit 5/jam/IP, simpan ke
  `data/usulan.json`, write-only, tidak pernah disajikan publik).
- `mascot.png` — maskot P.

## Infra

- `peka-tools.service` (lokal 8094) + `peka-tools-tunnel.service`
  (remote 18092) + Caddy `tools.peka.ai.id` → 127.0.0.1:18092.
  Unit file di `~/workspace/9router-setup/scripts/`, ikut
  `recover-after-reboot.sh`. DNS `tools` → 43.156.63.114 ditambah manual.
- Link masuk: `url.peka.ai.id` (seksi TOOLS) + web utama `peka.ai.id`.

## Tambah edisi baru

1. Setelah edisi SPILL tayang, tambah 5 entri ke `tools.json` (salin
   format entri yang ada: name, stars, forks, language, license, desc,
   cocok, category, edition, edition_label, date, blog, tags).
2. Tambah satu baris ke `archive` (label, date, count, blog, edition).
3. Angka bisa diambil dari `https://api.github.com/repos/<owner>/<repo>`.
4. Tidak perlu restart service: `tools.json` dibaca tiap load halaman.
   Koleksi baru: tambah objek ke `collections` dengan `members` berisi
   nama repo persis seperti di `tools`.

## Usulan masuk

Cek `data/usulan.json` secara berkala. Usulan direview manual; yang
lolos standar kurasi (repo asli terverifikasi, ringkasan jujur) baru
masuk edisi berikutnya dan katalog.
