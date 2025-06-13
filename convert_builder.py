import os
from os import path, mkdir
import xml.etree.ElementTree as ET
from lcpcli.builder import Corpus
import nltk
import os
import shutil
import re
from tqdm import tqdm
import json
from datetime import datetime

### Check NE XMLs

if not path.exists("output"):
    mkdir("output")

# Find directory starting with "SSRQ" and containing a "data" subfolder
dir = None
current_dir = os.getcwd()
for item in os.listdir(current_dir):
    if item.startswith("SSRQ") and os.path.isdir(item):
        potential_data_path = os.path.join(current_dir, item, "data")
        if os.path.isdir(potential_data_path):
            dir = potential_data_path
            break

if not dir:
    raise FileNotFoundError(
        "Could not find a directory starting with 'SSRQ' containing a 'data' subfolder"
    )

# Find all subdirectories
subdirs = []
for item in os.listdir(dir):
    item_path = os.path.join(dir, item)
    if os.path.isdir(item_path):
        subdirs.append(item_path)

# Dictionary to store XML files for each subdirectory
xml_files_by_subdir = {}

# Loop through each subdirectory
for subdir_path in subdirs:
    subdir_name = os.path.basename(subdir_path)
    xml_files_by_subdir[subdir_name] = []

    # Look for folders within this subdirectory
    for inner_folder in os.listdir(subdir_path):
        inner_folder_path = os.path.join(subdir_path, inner_folder)
        if os.path.isdir(inner_folder_path):
            # Look for XML files in inner folders
            for file in os.listdir(inner_folder_path):
                if "-lit" in file or "-intro" in file:
                    continue
                if file.endswith(".xml"):
                    xml_files_by_subdir[subdir_name].append(
                        os.path.join(inner_folder_path, file)
                    )

# Register namespaces for ElementTree
namespaces = {
    "tei": "http://www.tei-c.org/ns/1.0",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "ssrq": "http://ssrq-sds-fds.ch/ns/nonTEI",
}

for prefix, uri in namespaces.items():
    ET.register_namespace(prefix, uri)


def extract_metadata(root):
    """Extract metadata from the TEI header."""
    metadata = {}

    # Extract title
    title_elem = root.find(".//tei:titleStmt/tei:title", namespaces)
    if title_elem is not None and title_elem.text:
        metadata["title"] = remove_extra_spaces(title_elem.text.strip()).replace(
            "\n", " "
        )

    # Extract editors
    editors = []
    for editor in root.findall(".//tei:titleStmt/tei:editor/tei:persName", namespaces):
        if editor.text:
            editors.append(editor.text.strip())
    metadata["editors"] = editors

    # Extract dates
    for date in root.findall(".//tei:publicationStmt/tei:date", namespaces):
        date_type = date.get("type")
        date_when = date.get("when")
        if date_type and date_when:
            metadata[f"date_{date_type}"] = date_when

    # Extract repository information
    for repo in root.findall(".//tei:msIdentifier/tei:repository", namespaces):
        lang = repo.get("{http://www.tei-c.org/ns/1.0}lang")
        if repo.text and lang:
            metadata[f"repository_{lang}"] = repo.text.strip()

    # Extract origination date
    orig_date = root.find(".//tei:origin/tei:origDate", namespaces)
    if orig_date is not None:
        metadata["origDate_when"] = orig_date.get("when", "")

    return metadata


##################################################################


def get_element_text(elem):
    """Extract all text content from an element and its children."""
    if elem is None:
        return ""
    text = elem.text or ""
    for child in elem:
        text += get_element_text(child)
        if child.tail:
            text += child.tail
    return text


def remove_extra_spaces(text):
    """Remove extra whitespaces from text."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


### Consider using "current_pos" from one text to another to linearize them at this point correctly


def extract_text_and_annotations(root, namespaces, current_pos=0):
    """
    Extract the full text content and annotations from a TEI XML document.

    Args:
        root: The XML root element
        namespaces: Namespace dictionary for XML parsing

    Returns:
        tuple: (text, annotations)
    """
    # Find the body containing the main text
    body = root.find(".//tei:body", namespaces)
    if body is None:
        return "", []

    # Initialize text and annotations lists
    text = []
    annotations = []

    # Track the position in the text
    current_pos = current_pos

    # Process the document to extract text and annotations
    position = process_element(body, text, annotations, current_pos, namespaces)

    return " ".join(text), annotations, position


def process_element(elem, text, annotations, position, namespaces):
    """
    Process an XML element to extract text and annotations recursively.

    Args:
        elem: The current XML element
        text: List to accumulate text (modified in-place)
        annotations: List to accumulate annotations (modified in-place)
        position: Current position in the text
        namespaces: XML namespace dictionary

    Returns:
        int: The new current position after processing this element
    """
    start_pos = position

    # Process element text (if any)
    elem_text = elem.text
    if elem_text:
        elem_text = remove_extra_spaces(elem_text)
        if elem_text:
            text.append(elem_text)
            position += len(elem_text) + 1
    # Get element tag name without namespace
    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

    # Special handling for choice tags
    if tag == "choice":
        abbr = elem.find("./tei:abbr", namespaces)
        expan = elem.find("./tei:expan", namespaces)

        # Track the start position for this annotation
        choice_start = position
        # "default" values
        abbr_text = ""
        expanded_text = ""

        # Use expanded text in the annotation
        if abbr is not None:
            abbr_text = remove_extra_spaces(get_element_text(abbr))

        # Get expanded text for the annotation
        if expan is not None:
            expanded_text = remove_extra_spaces(get_element_text(expan))
            text.append(expanded_text)
            position += len(expanded_text) + 1

        # Create the choice annotation
        annotations.append(
            {
                "type": "choice",
                "start_offset": choice_start,
                "end_offset": position,
                "text": text[-1] if text[-1:] else "",
                "alternative_text": abbr_text,
                "attributes": elem.attrib,
            }
        )

        # Skip processing children since we handled them directly
        for child in elem:
            child_tail = child.tail
            child_tail = remove_extra_spaces(child_tail)
            if child_tail:
                text.append(child_tail)
                position += len(child_tail) + 1
        return position

    # Process child elements
    for child in elem:
        position = process_element(child, text, annotations, position, namespaces)

        # Don't forget the tail text (text that follows the child element)
        child_tail = child.tail
        child_tail = remove_extra_spaces(child_tail)
        if child_tail:
            text.append(child_tail)
            position += len(child_tail) + 1

    # Create annotation if this is one of the types we're looking for
    if tag in ["placeName", "persName", "orgName", "substitution"]:
        # Add this annotation to our list
        annotation = {
            "type": tag,
            "start_offset": start_pos,
            "end_offset": position,
            "text": text[-1] if text[-1:] else "",
            "attributes": elem.attrib,
        }
        annotations.append(annotation)
    # print("Text is: ", text)
    # print("Tag is: ", tag)
    # input("...")
    return position


def process_xml_file(file_path, namespaces, current_pos=0):
    """
    Process a TEI XML file to extract metadata, text, and annotations.

    Args:
        file_path: Path to the XML file
        namespaces: XML namespace dictionary

    Returns:
        dict: Document data with metadata, text, and annotations
    """
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()

        # if <text> tag and its children don't contain any text, skip the doc
        text_element = root.find(".//tei:body", namespaces)
        text_element = get_element_text(text_element).strip()
        if (not text_element) or (len(text_element) < 150):
            with open("logger.txt", "a") as log_file:
                log_file.write(
                    f"Skipping document with no or little text content: {file_path}\n"
                )
            return {
                "file_path": file_path,
                "metadata": {},
                "text": "",
                "annotations": [],
            }, current_pos

        # Extract metadata
        metadata = extract_metadata(root)

        # Extract text and annotations
        text, annotations, current_pos = extract_text_and_annotations(
            root, namespaces, current_pos=current_pos
        )

        return {
            "file_path": file_path,
            "metadata": metadata,
            "text": text,
            "annotations": annotations,
        }, current_pos

    except Exception:
        print(f"Error processing {file_path}")
        return {
            "file_path": file_path,
            "metadata": {},
            "text": "",
            "annotations": [],
        }, current_pos


def process_corpus(xml_files_by_subdir):
    """
    Process all XML files in the corpus.

    Args:
        xml_files_by_subdir: Dictionary mapping subdirectory names to lists of XML file paths

    Returns:
        dict: Corpus data organized by subdirectory
    """

    # Create or empty the logger.txt file
    with open("logger.txt", "w") as f:
        f.write(f"Process started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    corpus_data = []
    current_pos = 0
    for subdir_name, xml_files in xml_files_by_subdir.items():
        print(f"Processing files from {subdir_name}...")

        for xml_file in tqdm(xml_files, desc=f"{subdir_name}", unit="file"):
            try:
                doc_data, current_pos = process_xml_file(
                    xml_file, namespaces, current_pos=current_pos
                )
                corpus_data.append((doc_data, subdir_name))
            except Exception as e:
                # Log the error but continue with next file
                print(f"Error processing {xml_file}: {e}")
                with open("logger.txt", "a") as log_file:
                    log_file.write(f"Error processing {xml_file}: {str(e)}\n")

    return corpus_data


corpus_data = process_corpus(xml_files_by_subdir)

##################################################################


def convert_to_lcp(corpus_data, output_folder="output"):
    """
    Convert corpus data to LCP format using lcpcli.builder.

    Args:
        corpus_data: List of tuples containing (doc_data, subdir_name)
        output_folder: Path to save the LCP corpus

    Returns:
        The created corpus object
    """
    corpus_data = corpus_data[:1000].copy()  # for testing purposes

    # Ensure NLTK resources are available for tokenization
    try:
        nltk.data.find("tokenizers/punkt")
    except LookupError:
        nltk.download("punkt", quiet=True)

    # Prepare output directory
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    else:
        # If folder exists, remove all files inside it
        for item in os.listdir(output_folder):
            item_path = os.path.join(output_folder, item)
            if os.path.isfile(item_path):
                os.remove(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)

    corpus = Corpus(
        "SSRQ Corpus", document="Document", segment="Sentence", token="Word"
    )

    for doc_data, subdir_name in corpus_data:
        if not doc_data.get("text") or not doc_data["text"].strip():
            print(
                f"Skipping empty document: {doc_data.get('file_path', 'Unknown file')}"
            )
            continue

        metadata = doc_data.get("metadata", {})
        doc_title = metadata.get(
            "title", "Untitled Document"  # Default title if not provided
        )
        doc_title = remove_extra_spaces(doc_title).replace("\n", " ")
        doc_text = doc_data["text"]
        annotations = doc_data.get("annotations", [])

        document_sentences = []
        for sentence_text in nltk.sent_tokenize(doc_text):
            sentence_words = []
            for word_form in nltk.word_tokenize(sentence_text):
                word_obj = corpus.Word(word_form)
                # word_obj.make()
                sentence_words.append(word_obj)

            sentence_obj = corpus.Sentence(*sentence_words, original=sentence_text)
            sentence_obj.make()
            document_sentences.append(sentence_obj)

        doc_obj = corpus.Document(*document_sentences, title=doc_title)

        for key, value in metadata.items():
            if key == "origDate_when":
                key = "origdate"  # Normalize key for origDate
            attr_name = key.lower()
            if attr_name and isinstance(value, (str, int, float, bool, list)):
                try:
                    setattr(doc_obj, attr_name, value)
                except AssertionError as e:
                    print(
                        f"Warning: Could not set document attribute '{attr_name}' (from key '{key}'): {e}"
                    )

        doc_obj.canton = subdir_name  # This is a valid attribute name
        doc_obj.make()

        for annotation in annotations:
            ann_type = annotation["type"]
            start_offset = annotation["start_offset"]
            end_offset = annotation["end_offset"]
            ann_text = annotation.get("text", "")

            layer_name_str = ann_type.capitalize()
            if not layer_name_str:
                print(
                    f"Warning: Could not derive valid layer name from type '{ann_type}'. Skipping annotation."
                )
                continue

            try:
                AnnotationLayerFactory = getattr(corpus, layer_name_str)
            except AttributeError:
                print(
                    f"Warning: LCP Layer factory for '{layer_name_str}' (from type '{ann_type}') not found. Skipping annotation: {annotation}"
                )
                continue

            ann_instance = AnnotationLayerFactory()

            if ann_type == "choice" and "alternative_text" in annotation:
                alt_text = annotation["alternative_text"]
                if not alt_text:
                    continue
                if alt_text:  # Only set if non-empty
                    ann_instance.alternativetext = (
                        alt_text  # Assumes 'alternativeText' is a valid attr name
                    )
            elif ann_text:
                # For other types, we can set the text directly
                ann_instance.text = ann_text

            for attr_key, attr_value in annotation.get("attributes", {}).items():
                safe_attr_key = attr_key.lower().replace("_", "").replace(" ", "")
                if safe_attr_key:
                    try:
                        setattr(ann_instance, safe_attr_key, attr_value)
                    except AssertionError as e:
                        print(
                            f"Warning: Could not set annotation attribute '{safe_attr_key}' (from key '{attr_key}') on layer {layer_name_str}: {e}"
                        )

            ann_instance.set_char(start_offset, end_offset)
            ann_instance.make()

    corpus.make(output_folder)
    print(f"Corpus successfully converted and saved to {output_folder}")


convert_to_lcp(corpus_data, output_folder="output")

from lcpcli.check_files import Checker

output_folder = "output"
conf = json.loads(open("output/config.json", "r").read())
checker = Checker(conf)
checker.run_checks(output_folder, full=True, add_zero=False)
