"""Toplayıcının ağ sınırlarını, tarih doğruluğunu ve kaynak biçimlerini ağ kullanmadan doğrular."""

import http.client
import io
import json
import re
import socket
import ssl
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import certifi

from radar import toplayici
from radar.konular import KONULAR


class ToplayiciTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "id": "anthropic", "name": "Anthropic", "kind": "html", "language": "en",
            "url": "https://www.anthropic.com/news", "fetch_url": "https://www.anthropic.com/news",
            "fetch_hosts": ["www.anthropic.com"], "item_hosts": ["www.anthropic.com"],
            "include_patterns": [r"^/news/[^/]+/?$"], "evidence_type": "uretici_duyurusu",
        }
        self.deadline = time.monotonic() + 35
        toplayici._ROBOTS_CACHE.clear()

    def test_kaynak_baglantisinda_sertifika_ve_alan_adi_dogrulamasi_zorunludur(self):
        connection = toplayici._PinnedHTTPSConnection(
            "www.wur.nl", socket.AF_INET, ("8.8.8.8", 443), 4)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.protocol, ssl.PROTOCOL_TLS_CLIENT)
        self.assertGreaterEqual(connection._context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_yuklenen_guven_kokleri_sabitlenmis_certifi_demetinden_gelir(self):
        bundle = Path(certifi.where()).read_text(encoding="ascii")
        certificates = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
                                  bundle, flags=re.DOTALL)
        expected = {ssl.PEM_cert_to_DER_cert(certificate) for certificate in certificates}
        self.assertTrue(expected)
        actual = set(toplayici._TLS_CONTEXT.get_ca_certs(binary_form=True))
        # Çalışma zamanı bazı köklerin reddedilmesini ayrıca yükleyebilir; bu
        # mevcut güvenlik kuralı kamusal CA demeti seçilirken korunmalıdır.
        runtime_roots = set(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).get_ca_certs(binary_form=True))
        self.assertEqual(actual, expected | runtime_roots)

    def test_yalniz_genel_internet_adresleri_kabul_edilir(self):
        denied = ["127.0.0.1", "10.0.0.2", "192.168.2.92", "172.16.2.3", "169.254.169.254",
                  "100.64.0.1", "100.100.100.100", "0.0.0.0", "192.0.2.1", "224.0.0.1",
                  "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "2001:db8::1",
                  "2002:7f00:1::", "2001::1", "invalid"]
        for address in denied:
            with self.subTest(address=address):
                self.assertFalse(toplayici._is_public_address(address))
        self.assertTrue(toplayici._is_public_address("8.8.8.8"))
        self.assertTrue(toplayici._is_public_address("2606:4700:4700::1111"))

    def test_adres_kimlik_port_sema_ve_alan_adi_sinirlari(self):
        for url in ["http://www.anthropic.com/news", "https://www.anthropic.com:8443/news",
                    "https://user:secret@www.anthropic.com/news", "https://www.anthropic.com.evil.test/news",
                    "https://127.0.0.1/", "https://blog.adobe.com/", "https://www.anthropic.com/news#part",
                    "https://www.anthropic.com/\r\nBad:yes", "https://www.anthropic.com\\@evil.test/"]:
            with self.subTest(url=url), self.assertRaises(toplayici.FetchError):
                toplayici._validate_url(url, {"www.anthropic.com"})
        self.assertEqual(toplayici._validate_url(self.source["url"], {"www.anthropic.com"}).hostname,
                         "www.anthropic.com")

    def test_karisik_dns_sonucunda_hicbir_adrese_baglanilmaz(self):
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 443))]
        with patch.object(toplayici.socket, "getaddrinfo", return_value=records), self.assertRaises(toplayici.FetchError):
            toplayici._resolve_public_host("www.anthropic.com", self.deadline)

    def test_tls_baglantisi_dogrulanan_ipyi_ve_alan_adini_kullanir(self):
        raw_socket = MagicMock()
        context = MagicMock()
        with patch.object(toplayici.socket, "socket", return_value=raw_socket), patch.object(toplayici.socket, "getaddrinfo") as dns:
            connection = toplayici._PinnedHTTPSConnection("www.anthropic.com", socket.AF_INET, ("8.8.8.8", 443), 4)
            connection._context = context
            connection.connect()
        dns.assert_not_called()
        raw_socket.connect.assert_called_once_with(("8.8.8.8", 443))
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="www.anthropic.com")

    def test_yonlendirme_izin_disina_cikamaz(self):
        redirect = toplayici.FetchResult(302, b"", {"location": "https://127.0.0.1/admin"}, self.source["url"])
        with patch.object(toplayici, "_robots_allowed", return_value=True), \
             patch.object(toplayici, "_request_once", return_value=redirect) as request, \
             self.assertRaises(toplayici.FetchError):
            toplayici._fetch(self.source["url"], {"www.anthropic.com"}, {}, self.deadline)
        self.assertEqual(request.call_count, 1)

    def test_yonlendirmede_dns_yeniden_dogrulanir(self):
        response = MagicMock()
        response.status = 302
        response.getheaders.return_value = [("Location", "/news/next")]
        response.read1.return_value = b""
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(toplayici, "_robots_allowed", return_value=True), \
             patch.object(toplayici, "_resolve_public_host", side_effect=[
                 (socket.AF_INET, ("8.8.8.8", 443)), toplayici.FetchError("Genel olmayan DNS sonucu.")]) as resolver, \
             patch.object(toplayici, "_PinnedHTTPSConnection", return_value=connection) as constructor, \
             self.assertRaises(toplayici.FetchError):
            toplayici._fetch(self.source["url"], {"www.anthropic.com"}, {}, self.deadline)
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(constructor.call_count, 1)

    def test_yonlendirme_dongusu_sinirlidir(self):
        redirect = toplayici.FetchResult(302, b"", {"location": "/news/loop"}, self.source["url"])
        with patch.object(toplayici, "_robots_allowed", return_value=True), \
             patch.object(toplayici, "_request_once", return_value=redirect) as request, \
             self.assertRaises(toplayici.FetchError):
            toplayici._fetch(self.source["url"], {"www.anthropic.com"}, {}, self.deadline)
        self.assertEqual(request.call_count, toplayici.MAX_REDIRECTS + 1)

    def test_bildirilen_buyuk_yanit_okunmadan_reddedilir(self):
        response = MagicMock()
        response.getheaders.return_value = [("Content-Length", "2000001")]
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(toplayici, "_resolve_public_host", return_value=(socket.AF_INET, ("8.8.8.8", 443))), \
             patch.object(toplayici, "_PinnedHTTPSConnection", return_value=connection), \
             self.assertRaises(toplayici.FetchError):
            toplayici._request_once(self.source["url"], {"www.anthropic.com"}, {}, self.deadline)
        response.read1.assert_not_called()
        connection.close.assert_called_once()

    def test_gizlenen_buyuk_govde_de_sinirlidir(self):
        response = MagicMock()
        response.getheaders.return_value = []
        response.read1.return_value = b"123456"
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(toplayici, "_resolve_public_host", return_value=(socket.AF_INET, ("8.8.8.8", 443))), \
             patch.object(toplayici, "_PinnedHTTPSConnection", return_value=connection), \
             self.assertRaises(toplayici.FetchError):
            toplayici._request_once(self.source["url"], {"www.anthropic.com"}, {}, self.deadline, max_bytes=5)

    def test_eksik_http_govdesi_basarili_icerik_sayilmaz(self):
        response_socket = MagicMock()
        response_socket.makefile.return_value = io.BytesIO(
            b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nabc")
        response = http.client.HTTPResponse(response_socket)
        response.begin()
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(toplayici, "_resolve_public_host", return_value=(socket.AF_INET, ("8.8.8.8", 443))), \
             patch.object(toplayici, "_PinnedHTTPSConnection", return_value=connection), \
             self.assertRaisesRegex(toplayici.FetchError, "eksik içerik"):
            toplayici._request_once(self.source["url"], {"www.anthropic.com"}, {}, self.deadline)
        connection.close.assert_called_once()

    def test_yavas_http_basligi_toplam_okuma_suresini_asamaz(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        address = listener.getsockname()
        stop = threading.Event()

        def serve():
            try:
                client, _ = listener.accept()
                with client:
                    client.settimeout(2)
                    client.recv(4096)
                    client.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nX-Slow: ")
                    for _ in range(30):
                        if stop.wait(0.06):
                            return
                        client.sendall(b"x")
                    client.sendall(b"\r\n\r\nOK")
            except OSError:
                return  # Süre sınırı nedeniyle istemci bağlantıyı kapatır.

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()

        def local_connection(host, family, verified_address, timeout, deadline=None):
            return http.client.HTTPConnection(verified_address[0], verified_address[1], timeout=timeout)

        started = time.monotonic()
        try:
            with patch.object(toplayici, "_resolve_public_host", return_value=(socket.AF_INET, address)), \
                 patch.object(toplayici, "_PinnedHTTPSConnection", side_effect=local_connection), \
                 self.assertRaises(toplayici.FetchError):
                toplayici._request_once(self.source["url"], {"www.anthropic.com"}, {}, started + 0.2)
            self.assertLess(time.monotonic() - started, 0.9)
        finally:
            stop.set()
            listener.close()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())

    def test_tam_http_govdesi_baglanti_kapaninca_da_kabul_edilir(self):
        response_socket = MagicMock()
        response_socket.makefile.return_value = io.BytesIO(
            b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 3\r\n\r\nabc")
        response = http.client.HTTPResponse(response_socket)
        response.begin()
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(toplayici, "_resolve_public_host", return_value=(socket.AF_INET, ("8.8.8.8", 443))), \
             patch.object(toplayici, "_PinnedHTTPSConnection", return_value=connection):
            result = toplayici._request_once(self.source["url"], {"www.anthropic.com"}, {}, self.deadline)
        self.assertEqual(result.body, b"abc")
        self.assertEqual(result.status, 200)

    def test_kosullu_http_yanitin_uzunlugu_govde_beklentisi_olmaz(self):
        response_socket = MagicMock()
        response_socket.makefile.return_value = io.BytesIO(
            b"HTTP/1.1 304 Not Modified\r\nContent-Length: 3000000\r\n\r\n")
        response = http.client.HTTPResponse(response_socket)
        response.begin()
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(toplayici, "_resolve_public_host", return_value=(socket.AF_INET, ("8.8.8.8", 443))), \
             patch.object(toplayici, "_PinnedHTTPSConnection", return_value=connection):
            result = toplayici._request_once(self.source["url"], {"www.anthropic.com"}, {}, self.deadline)
        self.assertEqual(result.status, 304)
        self.assertEqual(result.body, b"")

    def test_guvenilmeyen_tls_zinciri_anlasilir_hata_dondurur(self):
        connection = MagicMock()
        connection.request.side_effect = ssl.SSLCertVerificationError("untrusted chain")
        with patch.object(toplayici, "_resolve_public_host", return_value=(socket.AF_INET, ("8.8.8.8", 443))), \
             patch.object(toplayici, "_PinnedHTTPSConnection", return_value=connection), \
             self.assertRaisesRegex(toplayici.FetchError, "TLS sertifika zinciri"):
            toplayici._request_once(self.source["url"], {"www.anthropic.com"}, {}, self.deadline)
        connection.close.assert_called_once()

    def test_robots_yasagi_uygulanir_ve_onbellege_alinir(self):
        result = toplayici.FetchResult(200, b"User-agent: *\nDisallow: /news\n", {}, self.source["url"])
        with patch.object(toplayici, "_fetch", return_value=result) as fetch:
            self.assertFalse(toplayici._robots_allowed(self.source["url"], {"www.anthropic.com"}, self.deadline))
            self.assertFalse(toplayici._robots_allowed(self.source["url"], {"www.anthropic.com"}, self.deadline))
        self.assertEqual(fetch.call_count, 1)

    def test_robots_bilinmeyen_hata_izin_sayilmaz(self):
        result = toplayici.FetchResult(503, b"", {}, self.source["url"])
        with patch.object(toplayici, "_fetch", return_value=result), self.assertRaises(toplayici.FetchError):
            toplayici._robots_allowed(self.source["url"], {"www.anthropic.com"}, self.deadline)

    def test_robots_yoksa_tek_kaynak_okumasina_izin_verilir(self):
        result = toplayici.FetchResult(404, b"", {}, self.source["url"])
        with patch.object(toplayici, "_fetch", return_value=result):
            self.assertTrue(toplayici._robots_allowed(self.source["url"], {"www.anthropic.com"}, self.deadline))

    def test_rss_tarihleri_ve_metin_temizligi_korunur(self):
        feed = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title><item>
        <guid>post-1</guid><title>Useful release</title><link>https://www.anthropic.com/news/one?utm_source=x</link>
        <description>&lt;p&gt;Safe &lt;b&gt;summary&lt;/b&gt;&lt;/p&gt;&lt;script&gt;bad()&lt;/script&gt;</description>
        <pubDate>Sun, 25 Oct 2026 12:00:00 +0300</pubDate></item></channel></rss>'''
        item = toplayici._parse_feed(feed, self.source, self.source["url"])[0]
        self.assertEqual(item["published_at"], "2026-10-25T09:00:00Z")
        self.assertIsNone(item["updated_at"])
        self.assertIsNone(item["event_at"])
        self.assertEqual(item["excerpt"], "Safe summary")
        self.assertEqual(item["url"], "https://www.anthropic.com/news/one")

    def test_atom_arxiv_surumu_ayni_kimlikte_kalir(self):
        self.source.update(id="arxiv", item_hosts=["arxiv.org"], evidence_type="on_baski")
        feed = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Query</title><entry>
        <id>http://arxiv.org/abs/2610.12345v2</id><title>Greenhouse model</title>
        <link href="http://arxiv.org/abs/2610.12345v2"/><summary>Experiment</summary>
        <published>2026-10-22T12:00:00Z</published><updated>2026-10-25T12:00:00Z</updated></entry></feed>'''
        item = toplayici._parse_feed(feed, self.source, self.source["url"])[0]
        self.assertEqual(item["external_id"], "http://arxiv.org/abs/2610.12345")
        self.assertTrue(item["url"].startswith("https://arxiv.org/"))
        self.assertEqual(item["updated_at"], "2026-10-25T12:00:00Z")
        self.assertEqual(item["evidence_type"], "on_baski")
        previous = toplayici._parse_feed(feed.replace(b"12345v2", b"12345v1"), self.source, self.source["url"])[0]
        self.assertEqual(previous["external_id"], item["external_id"])
        self.assertNotEqual(previous["url"], item["url"])

    def test_html_yanit_ve_xml_varliklari_besleme_sayilmaz(self):
        for body in (b"<html><h1>Denied</h1></html>", b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///secret">]><rss/>'):
            with self.subTest(body=body), self.assertRaises(toplayici.FetchError):
                toplayici._parse_feed(body, self.source, self.source["url"])

    def test_cdata_icindeki_html_doctype_xml_varligi_sayilmaz(self):
        body = b'''<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
        <channel><title>Changelog</title><item><title>Release</title><link>https://www.anthropic.com/news/one</link>
        <description><![CDATA[<p>Safe description</p>]]></description>
        <content:encoded><![CDATA[<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN"
        "http://www.w3.org/TR/REC-html40/loose.dtd"><html><body><p>Article</p></body></html>]]></content:encoded>
        </item></channel></rss>'''
        with patch.object(toplayici, "_fetch") as network:
            result = toplayici._parse_feed(body, self.source, self.source["url"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["excerpt"], "Safe description")
        network.assert_not_called()

    def test_xml_varliklari_utf16_dahil_feedparserdan_once_reddedilir(self):
        declaration = '<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///private">]><rss><channel>&x;</channel></rss>'
        for body in (declaration.encode("utf-8"), declaration.encode("utf-16"),
                     b'<!DOCTYPE rss SYSTEM "https://evil.example/entity"><rss/>',
                     b'<rss><channel><![CDATA[safe]]><!ENTITY x "bad"></channel></rss>'):
            with self.subTest(encoding=body[:30]), patch.object(toplayici.feedparser, "parse") as parser:
                with self.assertRaises(toplayici.CollectionError):
                    toplayici._parse_feed(body, self.source, self.source["url"])
                parser.assert_not_called()

    def test_bos_gecerli_besleme_sahte_kayit_uretmez(self):
        body = b'<rss version="2.0"><channel><title>Empty</title></channel></rss>'
        self.assertEqual(toplayici._parse_feed(body, self.source, self.source["url"]), [])

    def test_atom_guncelleme_tarihi_yayin_tarihine_cevrilmez(self):
        feed = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>News</title><entry>
        <id>post-1</id><title>Useful release</title><link href="https://www.anthropic.com/news/release"/>
        <updated>2026-10-25T12:00:00Z</updated></entry></feed>'''
        item = toplayici._parse_feed(feed, self.source, self.source["url"])[0]
        self.assertIsNone(item["published_at"])
        self.assertEqual(item["updated_at"], "2026-10-25T12:00:00Z")

    def test_github_taslaklari_gizlenir_on_surum_etiketlenir(self):
        releases = [{"id": 1, "draft": True}, {"id": 2, "prerelease": True,
                    "name": "v2 beta", "html_url": "https://github.com/org/repo/releases/tag/v2",
                    "body": "<p>Fix</p>", "published_at": "2026-10-25T10:00:00Z"}]
        item = toplayici._parse_releases(json.dumps(releases).encode(), self.source, self.source["url"])[0]
        self.assertEqual(item["external_id"], "anthropic:2")
        self.assertEqual(item["evidence_type"], "on_surum")
        self.assertEqual(item["excerpt"], "Fix")
        self.assertIsNone(item["updated_at"])

    def test_github_hata_nesnesi_ve_bozuk_json_surum_sayilmaz(self):
        for body in (b'{"message":"rate limit"}', b'<!doctype html>'):
            with self.subTest(body=body), self.assertRaises(toplayici.FetchError):
                toplayici._parse_releases(body, self.source, self.source["url"])

    def test_html_yalniz_anlamli_izinli_haber_baglantisini_alir(self):
        body = b'''<nav><a href="/news/old">Navigation headline</a></nav><main>
        <article><a href="/news/new"><h2>New useful research</h2></a><p>A small experiment.</p>
        <time datetime="2026-10-25">October 25</time></article>
        <a href="/news/new">New useful research</a><a href="https://evil.test/news/a">Untrusted headline</a>
        <a href="javascript:alert(1)">Untrusted code</a><a href="/news/read">Read more</a>
        </main>'''
        items = toplayici._parse_html(body, self.source, self.source["url"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "New useful research")
        self.assertEqual(items[0]["published_at"], "2026-10-25")
        self.assertEqual(items[0]["excerpt"], "A small experiment.")

    def test_bilinmeyen_yayin_tarihi_doldurulmaz(self):
        body = b'<main><time datetime="2026-10-26">Site date</time><a href="/news/new">New useful research</a></main>'
        item = toplayici._parse_html(body, self.source, self.source["url"])[0]
        self.assertIsNone(item["published_at"])

    def test_html_karusel_sayaci_temizlenir_sayili_haber_paragrafi_korunur(self):
        examples = [
            ("1 / 5", ""),
            ("2/10", ""),
            ("<span>3</span>&nbsp;/&nbsp;<span>12</span>", ""),
            ("1 / 5 creators tested 4K video.", "1 / 5 creators tested 4K video."),
            ("Render time improved by 1 / 5.", "Render time improved by 1 / 5."),
            ("2026/10/26", "2026/10/26"),
        ]
        for paragraph, expected in examples:
            body = ("<main><ul><li><h3><a href='/news/new'>New useful video features</a></h3>"
                    f"<p>{paragraph}</p></li></ul></main>").encode()
            response = toplayici.FetchResult(200, body, {}, self.source["url"])
            with self.subTest(paragraph=paragraph), patch.object(toplayici, "_fetch", return_value=response):
                items = toplayici.collect_source(self.source)["items"]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["title"], "New useful video features")
                self.assertEqual(items[0]["excerpt"], expected)

    def test_html_yalniz_iso_tarih_ozeti_temizlenir_cumleler_ve_yayin_alani_korunur(self):
        examples = [
            ("2026-02-09 09:30", ""),
            ("2026-02-04 22:30", ""),
            ("2026-02-09", ""),
            ("2026-02-09T09:30:15Z", ""),
            ("2026-02-09T09:30:15.125+03:00", ""),
            ("2026/10/26", "2026/10/26"),
            ("2026-02-30", "2026-02-30"),
            ("2026-02-09 25:30", "2026-02-09 25:30"),
            ("Released on 2026-02-09 09:30.", "Released on 2026-02-09 09:30."),
            ("2026-02-09 brings new fixes.", "2026-02-09 brings new fixes."),
        ]
        for paragraph, expected in examples:
            body = ("<main><article><a href='/news/new'>Mosquitto Version 2.1.2</a>"
                    f"<p>{paragraph}</p></article></main>").encode()
            response = toplayici.FetchResult(200, body, {"last-modified": "Mon, 09 Feb 2026 09:30:00 GMT"},
                                             self.source["url"])
            with self.subTest(paragraph=paragraph), patch.object(toplayici, "_fetch", return_value=response):
                item = toplayici.collect_source(self.source)["items"][0]
                self.assertEqual(item["excerpt"], expected)
                self.assertIsNone(item["published_at"])
                self.assertIsNone(item["updated_at"])

    def test_butun_listeyi_saran_article_aciklamasi_haberlere_yazilmaz(self):
        body = b'''<main><article><p>Researchers publish technical articles here.</p>
        <time datetime="2026-10-26">List date</time><section>
        <h3><a href="/news/one">First greenhouse study</a></h3>
        <h3><a href="/news/two">Second greenhouse study</a></h3>
        </section></article></main>'''
        items = toplayici._parse_html(body, self.source, self.source["url"])
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item["excerpt"] == "" for item in items))
        self.assertTrue(all(item["published_at"] is None for item in items))

    def test_ic_makale_kapsayicisi_ozeti_ust_listeye_karistirilmaz(self):
        body = b'''<main><article><p>Generic listing introduction.</p>
        <article><a href="/news/one">First greenhouse study</a><p>First experiment.</p></article>
        <article><a href="/news/two">Second greenhouse study</a><p>Second experiment.</p></article>
        </article></main>'''
        items = toplayici._parse_html(body, self.source, self.source["url"])
        self.assertEqual([item["excerpt"] for item in items], ["First experiment.", "Second experiment."])

    def test_ayni_haberin_resim_ve_baslik_baglantisi_tek_kapsayicidir(self):
        body = b'''<main><article><a href="/news/one"><img src="photo.png"></a>
        <a href="/news/one">First greenhouse study</a><p>First experiment.</p>
        <time datetime="2026-10-25">October 25</time></article></main>'''
        items = toplayici._parse_html(body, self.source, self.source["url"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["excerpt"], "First experiment.")
        self.assertEqual(items[0]["published_at"], "2026-10-25")

    def test_dinamik_listenin_acik_kartinda_baslik_baglanti_komsusundan_alinir(self):
        self.source["title_from_context"] = True
        body = b'''<main><div class="marquee"><div><div><h2>First useful product update</h2>
        <p>First product explanation.</p><p><a href="/news/one">Read story</a></p></div></div></div>
        <div class="marquee"><div><div><h2>Second useful product update</h2>
        <p><a href="/news/two">Read story</a></p></div></div></div></main>'''
        items = toplayici._parse_html(body, self.source, self.source["url"])
        self.assertEqual([item["title"] for item in items], ["First useful product update", "Second useful product update"])
        self.assertEqual([item["excerpt"] for item in items], ["First product explanation.", ""])
        self.assertTrue(all(item["published_at"] is None for item in items))

    def test_ortak_liste_basligi_tekil_kart_basligi_olmaz(self):
        self.source["title_from_context"] = True
        body = b'''<main><div><h2>Explore all product updates</h2>
        <div><p><a href="/news/one">Read story</a></p></div>
        <div><p><a href="/news/two">Read story</a></p></div></div></main>'''
        with self.assertRaises(toplayici.FetchError):
            toplayici._parse_html(body, self.source, self.source["url"])

    def test_html_bos_ayristirma_basarili_kaynak_sayilmaz(self):
        with self.assertRaises(toplayici.FetchError):
            toplayici._parse_html(b"<h1>Enable JavaScript</h1>", self.source, self.source["url"])

    def test_proje_sayfasi_tarih_uydurmadan_sabit_kayit_uretir(self):
        self.source["kind"] = "project"
        body = b'<html><head><meta name="description" content="A research project"></head><main><h1>Greenhouse Challenge</h1></main></html>'
        item = toplayici._parse_html(body, self.source, self.source["url"])[0]
        self.assertEqual(item["external_id"], self.source["url"])
        self.assertEqual(item["excerpt"], "A research project")
        self.assertIsNone(item["published_at"])

    def test_sayfa_basina_elli_kayit_siniri_ilk_on_disindakileri_korur(self):
        body = ("<main>" + "".join(f'<a href="/news/item-{number}">Useful research {number}</a>' for number in range(70)) + "</main>").encode()
        items = toplayici._parse_html(body, self.source, self.source["url"])
        self.assertEqual(len(items), 50)
        self.assertTrue(items[-1]["url"].endswith("item-49"))

    def test_sayfalama_uc_sayfa_ve_kalici_devam_adresiyle_sinirlidir(self):
        self.source["kind"] = "github_releases"
        self.source["fetch_url"] = "https://api.github.com/repos/demo/project/releases?per_page=30"
        self.source["fetch_hosts"] = ["api.github.com"]
        def response(url, *args):
            if url == 'https://api.github.com/repos/demo/project':
                return toplayici.FetchResult(200, b'{"id":123,"archived":false,"disabled":false,"pushed_at":"2026-10-25T12:00:00Z"}', {}, url)
            page = int(__import__('urllib.parse', fromlist=['parse_qs']).parse_qs(__import__('urllib.parse', fromlist=['urlsplit']).urlsplit(url).query).get('page', ['1'])[0])
            body = json.dumps([{'id': page, 'name': f'Version {page}', 'html_url': f'https://github.com/demo/project/releases/tag/{page}'}]).encode()
            next_url = 'https://api.github.com/repositories/123/releases?per_page=30&page=' + str(page + 1)
            return toplayici.FetchResult(200, body, {'link': '<' + next_url + '>; rel="next"'}, url)
        with patch.object(toplayici, '_fetch', side_effect=response) as fetch:
            result = toplayici.collect_source(self.source)
        self.assertEqual(fetch.call_count, 4)
        self.assertEqual(len(result['items']), 3)
        self.assertTrue(result['next_url'].endswith('page=4'))
        with patch.object(toplayici, '_fetch', side_effect=response) as fetch:
            resumed = toplayici.collect_source(self.source, continuation=result['next_url'])
        self.assertEqual([item['external_id'] for item in resumed['items']], ['anthropic:1', 'anthropic:4', 'anthropic:5'])
        self.assertTrue(resumed['next_url'].endswith('page=6'))
        self.assertEqual(resumed['maintenance']['repository_id'], 123)
        self.assertIs(resumed['maintenance']['archived'], False)

    def test_github_canonical_sayfalama_dogrulanmamis_depo_numarasina_gidemez(self):
        source = {**self.source, 'kind':'github_releases', 'fetch_url':'https://api.github.com/repos/demo/project/releases', 'fetch_hosts':['api.github.com'], 'repository_id':123}
        with self.assertRaises(toplayici.CollectionError):
            toplayici._sayfa_adresi('https://api.github.com/repositories/456/releases?page=2', source)
        self.assertIn('/repositories/123/', toplayici._sayfa_adresi('https://api.github.com/repositories/123/releases?page=2', source))

    def test_arsivlenmis_proje_surumleri_korunur_ama_aktif_bakim_sayilmaz(self):
        source = {**self.source, 'kind':'github_releases', 'fetch_url':'https://api.github.com/repos/demo/project/releases', 'fetch_hosts':['api.github.com']}
        metadata = toplayici.FetchResult(200, b'{"id":123,"archived":true,"disabled":false,"pushed_at":"2025-01-01T00:00:00Z"}', {}, 'https://api.github.com/repos/demo/project')
        release = toplayici.FetchResult(200, b'[{"id":1,"name":"Release 1","html_url":"https://github.com/demo/project/releases/tag/1","published_at":"2024-12-20T00:00:00Z"}]', {}, source['fetch_url'])
        with patch.object(toplayici, '_fetch', side_effect=[metadata, release]):
            result = toplayici.collect_source(source)
        self.assertTrue(result['maintenance']['archived'])
        self.assertEqual(result['maintenance']['status'], 'ok')
        self.assertEqual(result['maintenance']['last_release_at'], '2024-12-20T00:00:00Z')
        self.assertEqual(len(result['items']), 1)

    def test_bozuk_devam_adresi_izinli_olsa_bile_baska_kaynak_yoluna_gidemez(self):
        for continuation in ['https://www.anthropic.com/private', 'https://127.0.0.1/news', 'https://evil.example/news']:
            with self.subTest(url=continuation), patch.object(toplayici, '_fetch') as fetch, self.assertRaises(toplayici.CollectionError):
                toplayici.collect_source(self.source, continuation=continuation)
            fetch.assert_not_called()

    def test_sonraki_sayfa_hatasi_ilk_kayitlari_ve_devam_noktasini_korur(self):
        next_url = self.source['fetch_url'] + '?page=2'
        first = toplayici.FetchResult(200, b'<main><a href="/news/new">New useful research</a></main>', {'link': '<' + next_url + '>; rel="next"'}, self.source['fetch_url'])
        with patch.object(toplayici, '_fetch', side_effect=[first, toplayici.CollectionError('Deneme süre sınırı.')]):
            result = toplayici.collect_source(self.source)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['error'], 'Deneme süre sınırı.')
        self.assertEqual(result['next_url'], next_url)

    def test_ayni_sayfa_yinelenirse_sonsuz_devam_uretilmez(self):
        self.source.update(kind='rss', pagination='wordpress')
        body = b'<rss><channel><item><title>New useful research</title><link>https://www.anthropic.com/news/new</link></item></channel></rss>'
        response = toplayici.FetchResult(200, body, {}, self.source['fetch_url'])
        with patch.object(toplayici, '_fetch', return_value=response) as fetch:
            result = toplayici.collect_source(self.source)
        self.assertEqual(fetch.call_count, 2)
        self.assertIsNone(result['next_url'])

    def test_kosullu_yanit_tarih_ve_kayit_uydurmaz(self):
        result = toplayici.FetchResult(304, b"", {}, self.source["url"])
        with patch.object(toplayici, "_fetch", return_value=result) as fetch:
            actual = toplayici.collect_source(self.source, etag='"v1"', modified="Sun, 25 Oct 2026 10:00:00 GMT")
        self.assertTrue(actual["not_modified"])
        self.assertEqual(actual["items"], [])
        self.assertEqual(actual["etag"], '"v1"')
        self.assertEqual(fetch.call_args.args[2]["If-None-Match"], '"v1"')

    def test_baslik_enjeksiyonu_ve_kaynak_erisim_hatasi(self):
        result = toplayici.FetchResult(429, b"private-response", {}, self.source["url"])
        with patch.object(toplayici, "_fetch", return_value=result) as fetch, self.assertRaises(toplayici.FetchError) as raised:
            toplayici.collect_source(self.source, etag="bad\r\nSecret: bad")
        self.assertNotIn("If-None-Match", fetch.call_args.args[2])
        self.assertNotIn("private-response", str(raised.exception))
        self.assertNotIn(self.source["url"], str(raised.exception))

    def test_yeni_icerikte_kaldirilan_onbellek_basliklari_korunmaz(self):
        self.source["kind"] = "rss"
        body = b'<rss version="2.0"><channel><title>Empty</title></channel></rss>'
        result = toplayici.FetchResult(200, body, {}, self.source["url"])
        with patch.object(toplayici, "_fetch", return_value=result):
            actual = toplayici.collect_source(self.source, etag='"v1"', modified="Sun, 25 Oct 2026 10:00:00 GMT")
        self.assertFalse(actual["not_modified"])
        self.assertIsNone(actual["etag"])
        self.assertIsNone(actual["modified"])

    def test_ilk_okumada_304_yaniti_basarili_tarama_sayilmaz(self):
        result = toplayici.FetchResult(304, b"", {}, self.source["url"])
        with patch.object(toplayici, "_fetch", return_value=result), self.assertRaises(toplayici.FetchError):
            toplayici.collect_source(self.source)

    def test_atom_yontemi_de_gecerli_besleme_okur(self):
        self.source["kind"] = "atom"
        body = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>News</title></feed>'
        result = toplayici.FetchResult(200, body, {}, self.source["url"])
        with patch.object(toplayici, "_fetch", return_value=result):
            actual = toplayici.collect_source(self.source)
        self.assertFalse(actual["not_modified"])
        self.assertEqual(actual["items"], [])

    def test_tarih_gun_hassasiyeti_ve_bozuk_tarih(self):
        self.assertEqual(toplayici._date("2026-10-25"), "2026-10-25")
        self.assertEqual(toplayici._date("2026-10-25T12:30:00+03:00"), "2026-10-25T09:30:00Z")
        self.assertIsNone(toplayici._date("2026-02-30"))
        self.assertIsNone(toplayici._date("not a date"))

    def test_kaynak_katalogu_registry_alanlarini_ve_izinli_adresleri_kapsar(self):
        path = Path(__file__).resolve().parents[1] / "radar" / "kaynaklar.json"
        sources = json.loads(path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(sources), 12)
        self.assertEqual(len({source["id"] for source in sources}), len(sources))
        self.assertEqual({source["topic"] for source in sources}, set(KONULAR))
        for source in sources:
            with self.subTest(source=source["id"]):
                toplayici._validate_url(source["fetch_url"], set(source["fetch_hosts"]))
                self.assertGreaterEqual(source["interval_hours"], 24)


if __name__ == "__main__":
    unittest.main()
