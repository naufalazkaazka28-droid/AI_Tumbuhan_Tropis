# Tumbuhan Tropis Saja

Chatbot pengetahuan tumbuhan tropis berbasis aturan. Chatbot tidak memakai API AI, layanan cloud, WhatsApp, ataupun endpoint chat. Pertanyaan diproses di browser menggunakan basis pengetahuan lokal.

## Menjalankan

Python hanya diperlukan untuk menyajikan file aplikasi dan basis pengetahuan secara lokal:

```powershell
python server.py
```

Buka http://127.0.0.1:8000. Server hanya melayani file statis yang telah ditentukan; semua route API seperti `/api/chat`, `/api/poll`, route admin, dan webhook WhatsApp tidak tersedia. Tidak ada koneksi keluar ke layanan chatbot.

## Cara kerja

| Bagian | Implementasi |
|---|---|
| Antarmuka dan pemrosesan pertanyaan | `index.html`, `app.js` |
| Basis pengetahuan | `tropical_plants_kb.json`, dimuat sebagai file statis lokal |
| Pencocokan | Normalisasi teks, koreksi typo, pencocokan nama/alias, intent, FAQ, konteks, dan fallback di browser |
| Penyajian file lokal | `server.py` (tidak memproses pesan) |

Chatbot memuat konteks pertanyaan lanjutan ke `sessionStorage`. Pertanyaan yang belum terjawab disimpan di `localStorage` perangkat, maksimal 100 entri. Data tidak dikirim ke server atau layanan lain. Menghapus data situs akan menghapus riwayat lokal tersebut.

## Pengetahuan yang tersedia

Basis pengetahuan berisi lebih dari 60 profil tumbuhan, nama ilmiah dan alias, serta FAQ tentang pengertian, ciri, jenis, habitat, manfaat, perkembangbiakan, ancaman, dan pelestarian tumbuhan tropis. Untuk mengubah pengetahuan, edit `tropical_plants_kb.json`, lalu muat ulang halaman.

Contoh tumbuhan hutan hujan tropis di basis pengetahuan dirangkum dari artikel [10+ Tumbuhan Hutan Hujan Tropis Indonesia dan Ciri-Ciri Khasnya](https://tekniksipil.id/10-tumbuhan-hutan-hujan-tropis-indonesia-dan-ciri/). Profil juga mencakup daftar 60 tumbuhan yang diberikan untuk chatbot; nama kelompok seperti `spp.` mencakup beberapa spesies.

## Pengujian

```powershell
python -m unittest test_chatbot test_server -v
```

`test_chatbot.py` menguji engine referensi Python, sedangkan aplikasi web aktif memakai implementasi browser di `app.js`. `test_server.py` memastikan server hanya melayani aset statis dan route API/WhatsApp tidak tersedia.

## Batasan

Chatbot memakai pencocokan berbasis aturan dan data tetap, bukan model generatif; jawaban hanya tersedia jika tercakup oleh basis pengetahuan. Informasi herbal bersifat edukatif dan bukan pengganti saran tenaga kesehatan.
