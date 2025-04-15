#!/usr/bin/env python3
"""
XLIFF Translator Tool

Un script per tradurre solo i file XLIFF all'interno dei bundle di localizzazione Xcode.
Questo script:
1. Prende un bundle .xcloc come input
2. Estrae e analizza i file XLIFF
3. Traduce le stringhe utilizzando l'API di Google Translate
4. Aggiorna solo i file XLIFF con le nuove traduzioni
5. Mantiene intatti i file xcstrings originali
6. Aggiorna solo contents.json con il target locale

Utilizzo:
    python xliff_translator.py --input <input_xcloc_path> --output <output_xcloc_path> --target_lang <target_language_code>
"""

import os
import sys
import shutil
import json
import xml.etree.ElementTree as ET
import argparse
import re
from pathlib import Path
import logging
from concurrent.futures import ThreadPoolExecutor
import time

# For translation
try:
    from googletrans import Translator
except ImportError:
    print("Installa il pacchetto googletrans: pip install googletrans==4.0.0-rc1")
    sys.exit(1)

# Configurazione logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('xliff_translation.log')
    ]
)
logger = logging.getLogger(__name__)

# Registra namespaces per XML
ET.register_namespace('', 'urn:oasis:names:tc:xliff:document:1.2')
ET.register_namespace('xsi', 'http://www.w3.org/2001/XMLSchema-instance')

# Costanti
FORMAT_SPECIFIER_PATTERN = re.compile(r'(%[@%diouxXfeEgGcs]|\{[^}]*\})')
PLACEHOLDER_TEMPLATE = "PLACEHOLDERXYZ{}"

# Mappatura codici lingua per Google Translate
# Questa mappa i codici lingua di Xcode ai codici di Google Translate
# Il codice Xcode originale viene preservato in tutti i file e le directory
LANGUAGE_CODE_MAPPING = {
    'zh-Hans': 'zh-cn',  # Cinese Semplificato
    'zh-Hant': 'zh-tw',  # Cinese Tradizionale
    'en-GB': 'en',  # Inglese Britannico
    # Aggiungi altre mappature se necessario
}


class FormatSpecifierHandler:
    """Gestisce la conservazione dei specificatori di formato durante la traduzione."""

    @staticmethod
    def extract_placeholders(text):
        """
        Estrae i specificatori di formato dal testo e li sostituisce con segnaposto.
        Restituisce il testo modificato e una mappa di segnaposto agli specificatori originali.
        """
        if not text:
            return text, {}

        placeholder_map = {}
        count = 0

        def replace_func(match):
            nonlocal count
            placeholder = PLACEHOLDER_TEMPLATE.format(count)
            placeholder_map[placeholder] = match.group(0)
            count += 1
            return placeholder

        modified_text = FORMAT_SPECIFIER_PATTERN.sub(replace_func, text)
        return modified_text, placeholder_map

    @staticmethod
    def restore_placeholders(text, placeholder_map):
        """
        Ripristina i specificatori di formato originali dai segnaposto.
        """
        if not text or not placeholder_map:
            return text

        result = text
        for placeholder, original in placeholder_map.items():
            result = result.replace(placeholder, original)
        return result


class Translator:
    """Gestisce la traduzione del testo usando l'API Google Translate."""

    def __init__(self):
        self.translator = self._create_translator()
        self.cache = {}  # Cache per evitare chiamate API ridondanti

    def _create_translator(self):
        """Crea e restituisce un client Google Translate."""
        try:
            from googletrans import Translator as GoogleTranslator
            return GoogleTranslator()
        except Exception as e:
            logger.error(f"Impossibile inizializzare il traduttore: {e}")
            return None

    def _map_language_code(self, lang_code):
        """
        Mappa i codici lingua di Xcode ai codici lingua di Google Translate.
        Questo è usato solo internamente per le chiamate API.
        """
        return LANGUAGE_CODE_MAPPING.get(lang_code, lang_code)

    def translate(self, text, source_lang, target_lang):
        """
        Traduce il testo dalla lingua di origine alla lingua di destinazione.
        Gestisce i specificatori di formato e la memorizzazione nella cache.
        """
        if not text or text.strip() == '':
            return text

        # Mappa i codici lingua per Google Translate
        google_source_lang = self._map_language_code(source_lang)
        google_target_lang = self._map_language_code(target_lang)

        # Controlla prima la cache
        cache_key = f"{source_lang}:{target_lang}:{text}"
        if cache_key in self.cache:
            logger.debug(f"Uso traduzione in cache per: {text}")
            return self.cache[cache_key]

        # Estrai i specificatori di formato
        handler = FormatSpecifierHandler()
        modified_text, placeholder_map = handler.extract_placeholders(text)

        try:
            # Se il testo contiene solo segnaposto, non tradurre
            if all(p in placeholder_map for p in modified_text.split()):
                return text

            # Esegui la traduzione
            translation = self.translator.translate(
                modified_text,
                src=google_source_lang,
                dest=google_target_lang
            ).text

            # Ripristina i specificatori di formato
            final_translation = handler.restore_placeholders(translation, placeholder_map)

            # Memorizza il risultato nella cache
            self.cache[cache_key] = final_translation

            # Aggiungi un piccolo ritardo per evitare limiti API
            time.sleep(0.2)

            return final_translation

        except Exception as e:
            logger.error(f"Traduzione fallita per il testo '{text}': {e}")
            return text  # Restituisci il testo originale in caso di errore


class XliffTranslator:
    """Gestisce la traduzione dei file XLIFF."""

    def __init__(self, translator, source_lang, target_lang):
        self.translator = translator
        self.source_lang = source_lang
        self.target_lang = target_lang

    def translate_file(self, input_path, output_path):
        """
        Traduce un file XLIFF e salva la versione tradotta.
        """
        logger.info(f"Traduzione file XLIFF: {input_path}")

        try:
            # Analizza il file XLIFF
            tree = ET.parse(input_path)
            root = tree.getroot()

            # Aggiorna l'attributo target-language negli elementi file
            # Utilizziamo il target_lang originale (non la versione mappata di Google Translate)
            # per mantenere la coerenza con i nomi di cartella .lproj e contents.json
            file_elements_updated = 0
            for file_elem in root.findall('.//{urn:oasis:names:tc:xliff:document:1.2}file'):
                current_target = file_elem.get('target-language', '')
                if current_target != self.target_lang:
                    logger.info(f"Aggiornamento target-language da '{current_target}' a '{self.target_lang}'")
                    file_elem.set('target-language', self.target_lang)
                    file_elements_updated += 1

            logger.info(f"Aggiornato attributo target-language in {file_elements_updated} elementi file")

            # Verifica che tutti gli elementi file abbiano l'attributo target-language corretto
            for file_elem in root.findall('.//{urn:oasis:names:tc:xliff:document:1.2}file'):
                if file_elem.get('target-language', '') != self.target_lang:
                    logger.warning(
                        f"L'elemento file ha ancora un target-language non corretto: {file_elem.get('target-language', '')}")
                    # Forza l'aggiornamento dell'attributo
                    file_elem.set('target-language', self.target_lang)

            # Ottieni tutte le unità di traduzione
            trans_units = root.findall('.//{urn:oasis:names:tc:xliff:document:1.2}trans-unit')

            # Conteggio per il logging
            total_units = len(trans_units)
            translated_count = 0

            # Elabora ogni unità di traduzione
            for unit in trans_units:
                source = unit.find('.//{urn:oasis:names:tc:xliff:document:1.2}source')
                target = unit.find('.//{urn:oasis:names:tc:xliff:document:1.2}target')

                if source is not None:
                    source_text = source.text or ""

                    # Crea elemento target se non esiste
                    if target is None:
                        target = ET.SubElement(unit, '{urn:oasis:names:tc:xliff:document:1.2}target')

                    # Ottieni lo stato corrente
                    current_state = target.get('state', '')

                    # Traduci solo se necessario
                    if target.text is None or target.text.strip() == '' or current_state != 'translated':
                        logger.info(f"Originale: {source_text}")
                        translated_text = self.translator.translate(
                            source_text, self.source_lang, self.target_lang
                        )
                        logger.info(f"Tradotto: {translated_text}")

                        # Aggiorna target
                        target.text = translated_text
                        target.set('state', 'translated')
                        translated_count += 1

            # Scrivi il file tradotto
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            tree.write(output_path, encoding='utf-8', xml_declaration=True)

            logger.info(f"Tradotte {translated_count} su {total_units} stringhe in {input_path}")
            return True

        except Exception as e:
            logger.error(f"Errore nella traduzione del file XLIFF {input_path}: {e}")
            return False


def verify_xliff_consistency(xliff_path, target_lang):
    """
    Verifica che tutti gli elementi file in un file XLIFF abbiano il target-language corretto.
    Restituisce True se coerente, False se sono state trovate incoerenze.
    """
    try:
        tree = ET.parse(xliff_path)
        root = tree.getroot()

        inconsistent_files = []
        for file_elem in root.findall('.//{urn:oasis:names:tc:xliff:document:1.2}file'):
            current_target = file_elem.get('target-language', '')
            original_attr = file_elem.get('original', '(sconosciuto)')

            if current_target != target_lang:
                inconsistent_files.append((original_attr, current_target))

        if inconsistent_files:
            logger.warning(f"Trovati {len(inconsistent_files)} elementi file incoerenti in {xliff_path}:")
            for orig, lang in inconsistent_files:
                logger.warning(f"  File '{orig}' ha target-language='{lang}', dovrebbe essere '{target_lang}'")
            return False

        return True

    except Exception as e:
        logger.error(f"Errore nella verifica della coerenza XLIFF in {xliff_path}: {e}")
        return False


class XclocBundle:
    """Gestisce le operazioni su un bundle di localizzazione Xcode."""

    def __init__(self, input_path, output_path, target_lang):
        self.input_path = input_path
        self.output_path = output_path
        self.target_lang = target_lang
        self.contents_json_path = os.path.join(input_path, 'contents.json')
        self.translator = None
        self.source_lang = None

    def process(self):
        """
        Elabora il bundle xcloc:
        1. Copia il bundle originale
        2. Analizza contents.json per ottenere la lingua di origine
        3. Aggiorna contents.json con la lingua di destinazione
        4. Trova e traduce solo i file XLIFF
        5. Verifica la coerenza dei codici lingua in tutti i file
        """
        # Controlla se l'input esiste
        if not os.path.exists(self.input_path):
            logger.error(f"Il percorso di input non esiste: {self.input_path}")
            return False

        # Controlla se contents.json esiste
        if not os.path.exists(self.contents_json_path):
            logger.error(f"contents.json non trovato in {self.input_path}")
            return False

        # Analizza contents.json
        try:
            with open(self.contents_json_path, 'r', encoding='utf-8') as f:
                contents = json.load(f)
                self.source_lang = contents.get('developmentRegion', '')

                if not self.source_lang:
                    logger.error("Lingua di origine non trovata in contents.json")
                    return False

                logger.info(f"Lingua di origine: {self.source_lang}")
        except Exception as e:
            logger.error(f"Errore nella lettura di contents.json: {e}")
            return False

        # Inizializza il traduttore
        self.translator = Translator()

        # Crea la struttura delle directory di output
        try:
            # Se la directory di output esiste, rimuovila
            if os.path.exists(self.output_path):
                shutil.rmtree(self.output_path)

            # Copia l'intera directory di input in output
            shutil.copytree(self.input_path, self.output_path)

            # Aggiorna contents.json in output
            output_contents_path = os.path.join(self.output_path, 'contents.json')
            with open(output_contents_path, 'r', encoding='utf-8') as f:
                contents = json.load(f)

            # Memorizza la locale di destinazione originale se esiste
            original_target = contents.get('targetLocale', '')

            # Aggiorna con la nuova locale di destinazione
            contents['targetLocale'] = self.target_lang

            with open(output_contents_path, 'w', encoding='utf-8') as f:
                json.dump(contents, f, indent=2, ensure_ascii=False)

            if original_target and original_target != self.target_lang:
                logger.info(f"Aggiornata contents.json target locale da '{original_target}' a '{self.target_lang}'")
            else:
                logger.info(f"Aggiornata contents.json con target locale: '{self.target_lang}'")

        except Exception as e:
            logger.error(f"Errore nella configurazione della struttura di output: {e}")
            return False

        # Elabora i file XLIFF
        xliff_translator = XliffTranslator(self.translator, self.source_lang, self.target_lang)

        # Trova tutti i file XLIFF
        xliff_files = []
        for root, _, files in os.walk(self.input_path):
            for file in files:
                if file.endswith('.xliff'):
                    input_file = os.path.join(root, file)
                    # Crea il percorso di output corrispondente
                    rel_path = os.path.relpath(input_file, self.input_path)
                    output_file = os.path.join(self.output_path, rel_path)
                    xliff_files.append((input_file, output_file))

        # Elabora i file XLIFF
        success = True
        for input_file, output_file in xliff_files:
            if not xliff_translator.translate_file(input_file, output_file):
                success = False

        # Esegui una verifica aggiuntiva su tutti i file XLIFF per garantire la coerenza
        logger.info("Esecuzione della verifica finale della coerenza della lingua di destinazione XLIFF...")
        for _, output_file in xliff_files:
            if not verify_xliff_consistency(output_file, self.target_lang):
                logger.warning(f"La verifica XLIFF ha trovato incoerenze in {output_file}")
                # Prova un altro tentativo di correzione diretta
                try:
                    self._force_fix_xliff_target_language(output_file)
                except Exception as e:
                    logger.error(f"Impossibile forzare la correzione della lingua di destinazione XLIFF: {e}")

        # Verifica finale della coerenza dell'intero bundle
        self._verify_bundle_consistency()

        return success

    def _force_fix_xliff_target_language(self, xliff_path):
        """
        Forza la correzione dell'attributo target-language in tutti gli elementi file.
        Questo è un metodo di ultima risorsa per garantire la coerenza.
        """
        logger.info(f"Forzatura della correzione della lingua di destinazione in {xliff_path}")

        try:
            # Usa l'analisi XML grezza per modificare direttamente il file
            with open(xliff_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Usa regex per sostituire tutti gli attributi target-language
            pattern = r'target-language="[^"]*"'
            replacement = f'target-language="{self.target_lang}"'
            new_content = re.sub(pattern, replacement, content)

            # Scrivi di nuovo il contenuto corretto
            with open(xliff_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            logger.info(f"Correzione forzata della lingua di destinazione completata con successo in {xliff_path}")
        except Exception as e:
            logger.error(f"Errore durante la correzione forzata di {xliff_path}: {e}")
            raise

    def _verify_bundle_consistency(self):
        """
        Esegue una verifica finale dell'intero bundle per garantire la coerenza
        tra contents.json e i file XLIFF.
        """
        logger.info("Esecuzione della verifica finale della coerenza del bundle...")

        try:
            # 1. Controlla contents.json
            contents_path = os.path.join(self.output_path, 'contents.json')
            with open(contents_path, 'r', encoding='utf-8') as f:
                contents = json.load(f)

            contents_target = contents.get('targetLocale', '')
            if contents_target != self.target_lang:
                logger.error(f"contents.json targetLocale è '{contents_target}', dovrebbe essere '{self.target_lang}'")
                # Correggilo
                contents['targetLocale'] = self.target_lang
                with open(contents_path, 'w', encoding='utf-8') as f:
                    json.dump(contents, f, indent=2, ensure_ascii=False)
                logger.info("Corretta targetLocale in contents.json")

            # 2. Controlla tutti i file XLIFF un'ultima volta
            xliff_files = []
            for root, _, files in os.walk(self.output_path):
                for file in files:
                    if file.endswith('.xliff'):
                        xliff_files.append(os.path.join(root, file))

            for xliff_path in xliff_files:
                if not verify_xliff_consistency(xliff_path, self.target_lang):
                    # Se ancora incoerente dopo tutte le correzioni, registra un forte avviso
                    logger.error(f"CONTROLLO FINALE: {xliff_path} ha ancora incoerenze nel target-language!")

            logger.info("Verifica della coerenza del bundle completata")

        except Exception as e:
            logger.error(f"Errore durante la verifica della coerenza del bundle: {e}")
            # Questo è un passaggio di verifica, quindi non è necessario generare l'eccezione


def validate_language_code(lang_code):
    """
    Convalida che il codice lingua segua le convenzioni Xcode.
    Restituisce il codice lingua invariato per garantire la coerenza.
    """
    # Codici lingua comuni di Xcode da controllare
    valid_codes = [
        'en', 'fr', 'de', 'es', 'it', 'ja', 'ko', 'nl', 'pt', 'ru', 'sv',
        'zh-Hans', 'zh-Hant', 'ar', 'ca', 'cs', 'da', 'el', 'fi', 'he',
        'hi', 'hr', 'hu', 'id', 'ms', 'no', 'pl', 'pt-PT', 'ro', 'sk',
        'th', 'tr', 'uk', 'vi'
    ]

    if lang_code not in valid_codes:
        logger.warning(f"Il codice lingua '{lang_code}' non è un codice lingua standard di Xcode.")
        logger.warning("Questo potrebbe causare problemi con il sistema di localizzazione di Xcode.")
        logger.warning(f"I codici lingua comuni di Xcode includono: {', '.join(valid_codes[:10])}...")

    return lang_code


def main():
    parser = argparse.ArgumentParser(description='Traduce solo i file XLIFF nei bundle di localizzazione Xcode')
    parser.add_argument('--input', required=True, help='Percorso del bundle .xcloc di input')
    parser.add_argument('--output', required=True, help='Percorso del bundle .xcloc di output')
    parser.add_argument('--target_lang', required=True, help='Codice lingua di destinazione (es., en, fr, de, zh-Hans)')
    parser.add_argument('--verify-codes', action='store_true',
                        help='Verifica i codici lingua rispetto agli standard Xcode')

    args = parser.parse_args()

    # Convalida il codice lingua di destinazione se richiesto
    if args.verify_codes:
        target_lang = validate_language_code(args.target_lang)
    else:
        target_lang = args.target_lang

    # Elabora il bundle
    bundle = XclocBundle(args.input, args.output, target_lang)
    success = bundle.process()

    if success:
        logger.info(f"Elaborazione completata con successo da {args.input} a {args.output}")
        return 0
    else:
        logger.error(f"Impossibile elaborare {args.input}")
        return 1


if __name__ == "__main__":
    sys.exit(main())