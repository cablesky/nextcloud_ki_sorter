import os
import time
import logging
from typing import List, Dict, Tuple, Optional
import requests
from requests.auth import HTTPBasicAuth
from config import Config

logger = logging.getLogger("nc_client")

import urllib.parse

class NextcloudClient:
    """WebDAV Client für Nextcloud zum Herunterladen, Verschieben und Durchsuchen von Ordnern."""
    
    _folder_cache: Dict[str, Tuple[float, Dict[str, List[str]]]] = {}

    def __init__(self, user: str = None, password: str = None):
        # Für Team-Ordner / Group Folders wird stets der konfigurierte Service-Account (App-Passwort) genutzt
        self.user = Config.NEXTCLOUD_USER if Config.NEXTCLOUD_USER else (user or "")
        self.password = password or Config.NEXTCLOUD_PASSWORD
        self.base_url = Config.get_webdav_url(self.user)
        self.auth = HTTPBasicAuth(self.user, self.password)

    @classmethod
    def invalidate_cache(cls):
        """Leert den globalen Ordnerstruktur-Cache."""
        cls._folder_cache.clear()
        logger.debug("Ordnerstruktur-Cache wurde geleert.")

    @staticmethod
    def normalize_inbox_path(nc_file_path: str, user: Optional[str] = None) -> str:
        """Bereinigt den übergebenen Nextcloud-Dateipfad (Groupfolders, /files/-Präfix, Username)."""
        clean_path = nc_file_path.strip("/")
        inbox = Config.INBOX_FOLDER.strip("/")
        if "__groupfolders/" in clean_path:
            if "/files/" in clean_path:
                sub_path = clean_path.split("/files/", 1)[-1]
                clean_path = f"{inbox}/{sub_path}"
            else:
                filename = os.path.basename(clean_path)
                clean_path = f"{inbox}/{filename}"
        elif "files/" in clean_path:
            clean_path = clean_path.split("files/", 1)[-1]
        if user and clean_path.startswith(f"{user}/"):
            clean_path = clean_path[len(user)+1:]
        return clean_path

    def _full_url(self, path: str) -> str:
        clean_path = path.strip("/")
        if not clean_path:
            return self.base_url
        encoded_path = urllib.parse.quote(clean_path, safe="/")
        return f"{self.base_url}/{encoded_path}"

    def download_file(self, nc_path: str, local_destination: str) -> str:
        """Lädt eine Datei aus Nextcloud in einen lokalen Pfad herunter."""
        url = self._full_url(nc_path)
        logger.info(f"Lade Datei herunter: {url} -> {local_destination}")
        
        response = requests.get(url, auth=self.auth, stream=True)
        if response.status_code != 200:
            raise Exception(f"Fehler beim Download von Nextcloud ({response.status_code}): {response.text}")
            
        os.makedirs(os.path.dirname(local_destination), exist_ok=True)
        with open(local_destination, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return local_destination

    def upload_file(self, local_source: str, nc_path: str) -> bool:
        """Lädt eine lokale Datei hoch zu Nextcloud."""
        url = self._full_url(nc_path)
        logger.info(f"Lade Datei hoch: {local_source} -> {url}")
        
        with open(local_source, 'rb') as f:
            response = requests.put(url, auth=self.auth, data=f)
            
        if response.status_code not in (200, 201, 204):
            raise Exception(f"Fehler beim Upload ({response.status_code}): {response.text}")
        return True

    def file_exists(self, nc_path: str) -> bool:
        """Prüft per WebDAV PROPFIND, ob eine Datei oder ein Ordner in Nextcloud existiert."""
        url = self._full_url(nc_path)
        try:
            res = requests.request("PROPFIND", url, auth=self.auth, headers={"Depth": "0"})
            return res.status_code == 207
        except Exception:
            return False

    def resolve_filename_collision(self, target_nc_path: str) -> str:
        """Falls im Zielpfad bereits eine Datei existiert, wird ein Suffix (_1, _2, ...) angehängt."""
        if not self.file_exists(target_nc_path):
            return target_nc_path
            
        dir_name = os.path.dirname(target_nc_path)
        file_name = os.path.basename(target_nc_path)
        name_part, ext_part = os.path.splitext(file_name)
        
        counter = 1
        while counter < 100:
            candidate_filename = f"{name_part}_{counter}{ext_part}"
            candidate_path = f"{dir_name}/{candidate_filename}" if dir_name else candidate_filename
            if not self.file_exists(candidate_path):
                logger.info(f"Dateikollision in Nextcloud erkannt. Neuer Zielpfad: {candidate_path}")
                return candidate_path
            counter += 1
            
        return target_nc_path

    def move_file(self, source_nc_path: str, target_nc_path: str) -> Tuple[bool, str]:
        """Verschiebt/Benennt eine Datei in Nextcloud per WebDAV MOVE um und behebt ggf. Dateikollisionen. Gibt (True, finaler_pfad) zurück."""
        final_target_path = self.resolve_filename_collision(target_nc_path)
        source_url = self._full_url(source_nc_path)
        target_url = self._full_url(final_target_path)
        
        # Stelle sicher, dass Ziel-Ordner existieren
        target_dir = os.path.dirname(final_target_path)
        self.ensure_directory_exists(target_dir)

        logger.info(f"Verschiebe in Nextcloud: {source_nc_path} -> {final_target_path}")
        headers = {"Destination": target_url, "Overwrite": "F"}
        response = requests.request("MOVE", source_url, auth=self.auth, headers=headers)
        
        if response.status_code not in (201, 204):
            raise Exception(f"Fehler beim Verschieben ({response.status_code}): {response.text}")
        return True, final_target_path

    def relocate_files(self, source_paths: List[str], target_folder: str) -> List[Tuple[str, str]]:
        """Verschiebt eine Liste existierender Dateipfade in einen neuen Zielordner."""
        relocated = []
        target_dir = target_folder.strip("/")
        self.ensure_directory_exists(target_dir)

        for src in source_paths:
            src_clean = src.strip("/")
            if not src_clean or not self.file_exists(src_clean):
                logger.warning(f"Zu verschiebende Datei existiert nicht: {src_clean}")
                continue

            filename = os.path.basename(src_clean)
            target_path = f"{target_dir}/{filename}"

            # Verhindere Selbstverschiebung
            if src_clean == target_path:
                continue

            try:
                _, final_path = self.move_file(src_clean, target_path)
                logger.info(f"Alt-Dokument umsortiert: {src_clean} -> {final_path}")
                relocated.append((src_clean, final_path))
            except Exception as e:
                logger.error(f"Fehler beim Umsortieren von {src_clean}: {e}")

        if relocated:
            self.invalidate_cache()

        return relocated

    def ensure_directory_exists(self, nc_dir_path: str):
        """Erstellt rekursiv Unterordner in Nextcloud per WebDAV MKCOL, falls sie nicht existieren."""
        parts = [p for p in nc_dir_path.strip("/").split("/") if p]
        current_path = ""
        created_any = False
        
        for part in parts:
            current_path = f"{current_path}/{part}"
            url = self._full_url(current_path)
            res = requests.request("PROPFIND", url, auth=self.auth, headers={"Depth": "0"})
            if res.status_code == 404:
                logger.info(f"Erstelle Nextcloud-Ordner: {current_path}")
                mk_res = requests.request("MKCOL", url, auth=self.auth)
                if mk_res.status_code in (201, 204):
                    created_any = True
                elif mk_res.status_code == 405:
                    # 405 = Method Not Allowed (Ordner existiert ggf. bereits oder wurde parallel erstellt)
                    logger.debug(f"Ordner {current_path} existiert bereits (MKCOL 405).")
                else:
                    logger.warning(f"Konnte Ordner {current_path} nicht erstellen ({mk_res.status_code}): {mk_res.text}")

        if created_any:
            self.invalidate_cache()

    def get_folder_structure_and_samples(self, root_nc_path: str = None) -> Dict[str, List[str]]:
        """
        Scannt die Ordnerstruktur unter root_nc_path (z. B. /Dokumente)
        und gibt ein Dictionary zurück: { "Ordnerpfad": ["Beispieldatei1.pdf", "Beispieldatei2.pdf"] }
        Verwendet einen TTL-Cache zur Performance-Optimierung.
        """
        root_path = root_nc_path or Config.TARGET_ROOT_FOLDER
        now = time.time()

        # Cache-Prüfung
        if root_path in NextcloudClient._folder_cache:
            ts, cached_data = NextcloudClient._folder_cache[root_path]
            if (now - ts) < Config.FOLDER_CACHE_TTL:
                logger.info(f"Verwende gecachte Ordnerstruktur für '{root_path}' (Gültigkeit: {Config.FOLDER_CACHE_TTL}s).")
                return cached_data

        structure: Dict[str, List[str]] = {}
        
        def _scan(current_path: str):
            url = self._full_url(current_path)
            headers = {"Depth": "1"}
            body = """<?xml version="1.0" encoding="utf-8" ?>
            <D:propfind xmlns:D="DAV:">
                <D:prop>
                    <D:resourcetype/>
                    <D:displayname/>
                </D:prop>
            </D:propfind>"""
            
            try:
                res = requests.request("PROPFIND", url, auth=self.auth, headers=headers, data=body, timeout=15)
                if res.status_code != 207:
                    logger.warning(f"Konnte Ordner {current_path} nicht auslesen (Status {res.status_code})")
                    return

                import xml.etree.ElementTree as ET
                tree = ET.fromstring(res.content)
                
                subfolders = []
                files = []
                
                for response_node in tree.findall("{DAV:}response"):
                    href_node = response_node.find("{DAV:}href")
                    if href_node is None or not href_node.text:
                        continue
                    href = href_node.text
                    from urllib.parse import unquote
                    decoded_href = unquote(href)
                    
                    is_dir = False
                    propstat = response_node.find("{DAV:}propstat")
                    if propstat is not None:
                        prop = propstat.find("{DAV:}prop")
                        if prop is not None:
                            res_type = prop.find("{DAV:}resourcetype")
                            if res_type is not None and res_type.find("{DAV:}collection") is not None:
                                is_dir = True

                    if "/remote.php/dav/files/" in decoded_href:
                        after_dav = decoded_href.split("/remote.php/dav/files/", 1)[-1]
                        parts = after_dav.strip("/").split("/", 1)
                        rel_path = "/" + parts[1] if len(parts) > 1 else "/"
                    else:
                        rel_path = decoded_href

                    if rel_path.strip("/") == current_path.strip("/"):
                        continue # Eigener Ordner

                    if is_dir:
                        subfolders.append(rel_path)
                    else:
                        filename = os.path.basename(rel_path)
                        files.append(filename)

                structure[current_path] = files[:5] # Bis zu 5 Beispieldateien merken
                
                for sf in subfolders:
                    _scan(sf)

            except Exception as scan_err:
                logger.error(f"Fehler beim Scannen des Ordners {current_path}: {scan_err}")

        _scan(root_path)
        NextcloudClient._folder_cache[root_path] = (now, structure)
        return structure

