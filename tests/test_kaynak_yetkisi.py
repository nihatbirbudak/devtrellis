"""Kullanıcının eklediği kaynağın yalnız kendi kamusal alan adlarında okunabildiğini doğrular."""

import copy
import unittest
from unittest.mock import patch

from radar import toplayici
from radar.kaynak_havuzu import kaynaklari_oku


class KaynakYetkisiTestleri(unittest.TestCase):
    def test_yeni_kaynak_yalniz_tam_alan_adini_yetkilendirir(self):
        source = copy.deepcopy(kaynaklari_oku()[0])
        source.update(id="custom_sample", fetch_url="https://news.example.org/feed.xml",
                      url="https://news.example.org/", fetch_hosts=["news.example.org"],
                      item_hosts=["news.example.org"])
        with patch.object(toplayici, "APPROVED_FETCH_HOSTS", frozenset()):
            toplayici.kaynaklari_yetkilendir([source])
            self.assertEqual(toplayici.APPROVED_FETCH_HOSTS, frozenset(["news.example.org"]))
            toplayici._validate_url(source["fetch_url"], set(source["fetch_hosts"]))
            for url, allowed in [("https://other.example.org/", {"other.example.org"}),
                                 ("https://news.example.org/", {"another.example.org"})]:
                with self.subTest(url=url, allowed=allowed), self.assertRaises(toplayici.CollectionError):
                    toplayici._validate_url(url, allowed)

    def test_yerel_kaynak_izin_listesine_giremez(self):
        source = copy.deepcopy(kaynaklari_oku()[0])
        source.update(fetch_url="https://192.168.2.92/", fetch_hosts=["192.168.2.92"])
        with patch.object(toplayici, "APPROVED_FETCH_HOSTS", frozenset(["valid.example.org"])):
            with self.assertRaises(ValueError):
                toplayici.kaynaklari_yetkilendir([source])
            self.assertEqual(toplayici.APPROVED_FETCH_HOSTS, frozenset(["valid.example.org"]))

    def test_makale_alan_adlari_tam_katalog_dogrulaninca_okunabilir(self):
        source = copy.deepcopy(kaynaklari_oku()[0])
        source.update(fetch_url="https://api.example.org/", url="https://articles.example.org/",
                      fetch_hosts=["api.example.org"], item_hosts=["articles.example.org"])
        with patch.object(toplayici, "APPROVED_FETCH_HOSTS", frozenset()):
            toplayici.kaynaklari_yetkilendir([source])
            toplayici._validate_url("https://articles.example.org/post", set(source["item_hosts"]))
            with self.assertRaises(toplayici.CollectionError):
                toplayici._validate_url("https://api.example.org/post", set(source["item_hosts"]))
