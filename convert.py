import os
from os import path, mkdir
import xml.etree.ElementTree as ET
import re
import uuid
import csv
import json
from datetime import datetime

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


def process_corpus(xml_files_by_subdir):
    """Process all XML files in the corpus."""
    corpus_data = {}

    for subdir_name, file_paths in xml_files_by_subdir.items():
        subdir_data = []

        for file_path in file_paths:
            try:
                doc_data = extract_document_data(file_path)
                subdir_data.append(doc_data)
            except Exception as e:
                print(f"Error processing {file_path}: {str(e)}")

        corpus_data[subdir_name] = subdir_data

    return corpus_data


def extract_document_data(file_path):
    """Extract data from a single XML document."""
    tree = ET.parse(file_path)
    root = tree.getroot()

    # Extract document metadata
    metadata = extract_metadata(root)

    # Initialize text extraction data structures
    text_content = []  # Store final text directly, character by character
    annotations = []
    pages = []
    current_page = None
    context = {
        "text_content": text_content,
        "annotations": annotations,
        "pages": pages,
        "current_page": current_page,
        "global_offset": 0,
        "alternative_text": None,
        "last_char_is_whitespace": True,  # Track whitespace to avoid duplicates
    }

    # Process text content
    body = root.find(".//tei:body", namespaces)
    if body is not None:
        process_element(body, context)

    # Join text content into a single string
    text_content = "".join(context["text_content"])

    # Don't strip the text to preserve line breaks at start/end
    return {
        "file_path": file_path,
        "metadata": metadata,
        "text": text_content,
        "annotations": context["annotations"],
    }


def extract_metadata(root):
    """Extract metadata from the TEI header."""
    metadata = {}

    # Extract title
    title_elem = root.find(".//tei:titleStmt/tei:title", namespaces)
    if title_elem is not None and title_elem.text:
        metadata["title"] = title_elem.text.strip()

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

    # Extract IDs
    for idno in root.findall(".//tei:msIdentifier/tei:idno", namespaces):
        lang = idno.get("{http://www.tei-c.org/ns/1.0}lang")
        if idno.text:
            metadata[f"idno_{lang}"] = idno.text.strip()

    # Extract origination date
    orig_date = root.find(".//tei:origin/tei:origDate", namespaces)
    if orig_date is not None:
        metadata["origDate_when"] = orig_date.get("when", "")
        metadata["origDate_text"] = get_element_text(orig_date)

    return metadata


def process_element(element, context):
    """Process an XML element and its children recursively."""
    element_name = element.tag.split("}")[-1]
    start_offset = context["global_offset"]

    # Handle specific element types
    if element_name == "pb":
        page_num = element.get("n")
        if page_num:
            context["current_page"] = page_num
            context["pages"].append(
                {"page_num": page_num, "offset": context["global_offset"]}
            )

    elif element_name == "lb":
        # Always add a newline character for line breaks, regardless of previous whitespace
        context["text_content"].append("\n")
        context["global_offset"] += 1
        context["last_char_is_whitespace"] = (
            True  # Mark as whitespace for future processing
        )

    elif element_name == "choice":
        # Handle choice between abbreviated and expanded forms
        abbr = element.find(".//tei:abbr", namespaces)
        expan = element.find(".//tei:expan", namespaces)

        abbr_text = get_element_text(abbr) if abbr is not None else None
        expan_text = get_element_text(expan) if expan is not None else None

        # Use expanded text in the main content if available, otherwise use abbreviated
        chosen_text = expan_text if expan_text else abbr_text
        alternative_text = abbr_text if chosen_text == expan_text else expan_text

        if chosen_text:
            add_text_to_context(chosen_text, context)

        # Add as annotation
        context["annotations"].append(
            {
                "type": "choice",
                "text": chosen_text,
                "alternative_text": alternative_text,
                "start_offset": start_offset,
                "end_offset": context["global_offset"],
                "page": context["current_page"],
            }
        )

        # Skip processing children as we've handled them here
        return context["global_offset"]

    elif element_name == "note":
        # Add space before note in text if needed
        if not context["last_char_is_whitespace"]:
            context["text_content"].append(" ")
            context["global_offset"] += 1
            context["last_char_is_whitespace"] = True

        note_text = get_element_text(element)
        note_start = context["global_offset"]

        # Add as annotation
        context["annotations"].append(
            {
                "type": "note",
                "text": note_text,
                "alternative_text": None,
                "start_offset": note_start,
                "end_offset": note_start + len(note_text),
                "page": context["current_page"],
            }
        )

        return context["global_offset"]

    elif element_name == "subst":
        # Handle substitution (deleted and added text)
        del_elem = element.find(".//tei:del", namespaces)
        add_elem = element.find(".//tei:add", namespaces)

        del_text = get_element_text(del_elem) if del_elem is not None else None
        add_text = get_element_text(add_elem) if add_elem is not None else None

        # Only add the replacement text to the main content
        if add_text:
            add_text_to_context(add_text, context)

        # Add as annotation
        context["annotations"].append(
            {
                "type": "substitution",
                "text": add_text,
                "alternative_text": del_text,  # Store deleted text as alternative
                "start_offset": start_offset,
                "end_offset": context["global_offset"],
                "page": context["current_page"],
                "details": {"deleted": del_text, "added": add_text},
            }
        )

        # Skip processing children as we've handled them here
        return context["global_offset"]

    else:
        # Normal element processing
        if element.text:
            add_text_to_context(element.text, context)

        # Process children
        for child in element:
            process_element(child, context)

            # Add tail text after processing the child
            if child.tail:
                add_text_to_context(child.tail, context)

    # Check if this is an annotation element (not structural elements)
    if element_name not in [
        "div",
        "p",
        "body",
        "text",
        "group",
        "lb",
        "pb",
        "choice",
        "subst",
        "del",
        "add",
        "abbr",
        "expan",
    ]:
        # Don't annotate purely structural elements
        if element.attrib:
            # Extract the text content of this element
            element_text = get_element_text(element)

            context["annotations"].append(
                {
                    "type": element_name,
                    "attributes": dict(element.attrib),
                    "text": element_text,
                    "alternative_text": None,
                    "start_offset": start_offset,
                    "end_offset": context["global_offset"],
                    "page": context["current_page"],
                }
            )

    return context["global_offset"]


def add_text_to_context(text, context):
    """Add text to context, handling whitespace properly."""
    if not text:
        return

    # Clean text - collapse multiple spaces into one
    text = re.sub(r"\s+", " ", text)

    # Handle consecutive whitespace
    for char in text:
        if char.isspace():
            # Only add whitespace if previous character wasn't whitespace
            if not context["last_char_is_whitespace"]:
                context["text_content"].append(char)
                context["global_offset"] += 1
                context["last_char_is_whitespace"] = True
        else:
            context["text_content"].append(char)
            context["global_offset"] += 1
            context["last_char_is_whitespace"] = False


def get_element_text(element):
    """Extract the full text content of an element including children."""
    if element is None:
        return ""

    text_parts = []

    # Add element's direct text if it exists
    if element.text:
        text_parts.append(element.text)

    for child in element:
        if child.tag.split("}")[-1] not in ["del"]:  # Skip deleted text
            text_parts.append(get_element_text(child))
        if child.tail:
            text_parts.append(child.tail)

    # Join all text parts and normalize whitespace
    text = "".join(filter(None, text_parts))
    return remove_extra_spaces(text)


def remove_extra_spaces(text):
    """Remove extra spaces from the text."""
    return re.sub(r"\s{2,}", " ", text.strip())


### Example usage ###
a_file = extract_document_data(
    "SSRQ-SDS-FDS-editio-data-9247386/data/FR/FR_I_2_8/SSRQ-FR-I_2_8-208.17-1.xml"
)
### Need to check ranges on annotations ###


def convert_to_lcp(corpus_data, output_dir="output"):
    """
    Convert the extracted TEI documents to LCP format.

    Args:
        corpus_data: Dictionary with subdir_name -> list of document data
        output_dir: Directory to write LCP files to
    """

    # Create output directory if it doesn't exist
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Define annotation types based on the example data
    annotation_types = [
        "placeName",
        # "figure"
        # "hi",
        "choice",
        "origDate",
        # "term",
        "persName",
        "substitution",
        # "note",
        "date",
    ]

    # Track unique attribute values for lookup tables
    attribute_lookups = {
        "placeName_ref": {},
        # "hi_rend": {},
        "origDate_when": {},
        # "term_ref": {},
        "persName_ref": {},
        "date_dur_iso": {},
        # "note_text": {},
        "choice_alternative": {},
        "substitution_alternative": {},
    }

    # Open all required files
    with open(
        os.path.join(output_dir, "document.csv"), "w", newline="", encoding="utf-8"
    ) as doc_file, open(
        os.path.join(output_dir, "segment.csv"), "w", newline="", encoding="utf-8"
    ) as seg_file, open(
        os.path.join(output_dir, "token.csv"), "w", newline="", encoding="utf-8"
    ) as tok_file, open(
        os.path.join(output_dir, "token_form.csv"), "w", newline="", encoding="utf-8"
    ) as form_file, open(
        os.path.join(output_dir, "token_lemma.csv"), "w", newline="", encoding="utf-8"
    ) as lemma_file:

        # Initialize writers with headers
        doc_csv = csv.writer(doc_file)
        doc_csv.writerow(
            [
                "document_id",
                "char_range",
                "title",
                "editors",
                "date_electronic",
                "date_print",
                "orig_date",
                "canton",
            ]
        )

        seg_csv = csv.writer(seg_file)
        seg_csv.writerow(["segment_id", "char_range"])

        tok_csv = csv.writer(tok_file)
        tok_csv.writerow(
            ["token_id", "form_id", "lemma_id", "char_range", "segment_id"]
        )

        form_csv = csv.writer(form_file)
        form_csv.writerow(["form_id", "form"])

        lemma_csv = csv.writer(lemma_file)
        lemma_csv.writerow(["lemma_id", "lemma"])

        # Create files and writers for each annotation type
        annotation_files = {}
        annotation_writers = {}

        for ann_type in annotation_types:
            annotation_files[ann_type] = open(
                os.path.join(output_dir, f"{ann_type}.csv"),
                "w",
                newline="",
                encoding="utf-8",
            )
            annotation_writers[ann_type] = csv.writer(annotation_files[ann_type])

            # Define headers based on annotation type
            if ann_type == "placeName":
                annotation_writers[ann_type].writerow(["id", "char_range", "ref_id"])
            # elif ann_type == "hi":
            #     annotation_writers[ann_type].writerow(["id", "char_range", "rend"])
            elif ann_type == "choice":
                annotation_writers[ann_type].writerow(
                    ["id", "char_range", "alternative_id"]
                )
            elif ann_type == "origDate":
                annotation_writers[ann_type].writerow(["id", "char_range", "when"])
            # elif ann_type == "term":
            #     annotation_writers[ann_type].writerow(["id", "char_range", "ref_id"])
            elif ann_type == "persName":
                annotation_writers[ann_type].writerow(["id", "char_range", "ref_id"])
            elif ann_type == "substitution":
                annotation_writers[ann_type].writerow(
                    ["id", "char_range", "alternative_id"]
                )
            # elif ann_type == "note":
            #     annotation_writers[ann_type].writerow(["id", "char_range", "text_id"])
            elif ann_type == "date":
                annotation_writers[ann_type].writerow(["id", "char_range", "dur_iso"])

        # Create lookup tables for annotation attributes that need them
        lookup_files = {
            "placeName_ref": open(
                os.path.join(output_dir, "placeName_ref.csv"),
                "w",
                newline="",
                encoding="utf-8",
            ),
            # "term_ref": open(
            #     os.path.join(output_dir, "term_ref.csv"),
            #     "w",
            #     newline="",
            #     encoding="utf-8",
            # ),
            "persName_ref": open(
                os.path.join(output_dir, "persName_ref.csv"),
                "w",
                newline="",
                encoding="utf-8",
            ),
            # "note_text": open(
            #     os.path.join(output_dir, "note_text.csv"),
            #     "w",
            #     newline="",
            #     encoding="utf-8",
            # ),
            "choice_alternative": open(
                os.path.join(output_dir, "choice_alternative.csv"),
                "w",
                newline="",
                encoding="utf-8",
            ),
            "substitution_alternative": open(
                os.path.join(output_dir, "substitution_alternative.csv"),
                "w",
                newline="",
                encoding="utf-8",
            ),
        }

        lookup_writers = {key: csv.writer(file) for key, file in lookup_files.items()}

        # Write headers for lookup tables
        for key, writer in lookup_writers.items():
            if key in ["placeName_ref", "persName_ref"]:
                writer.writerow([f"{key}_id", key.split("_")[-1], "text"])
            else:
                writer.writerow([f"{key}_id", key.split("_")[-1]])

        # Initialize counters
        char_offset = 1  # Start from 1 as in LCP tutorial
        token_id = 1
        document_id = 1
        form_dict = {}  # Track unique forms
        annotation_counters = {ann_type: 1 for ann_type in annotation_types}

        # Process all documents
        for subdir_name, documents in corpus_data.items():
            for doc_data in documents:
                doc_start = char_offset
                text = doc_data["text"]
                metadata = doc_data["metadata"]
                annotations = doc_data.get("annotations", [])

                # Process segments (split by newline)
                segments = text.split("\n")
                for segment in segments:
                    segment = segment.strip()
                    if not segment:
                        continue

                    # Generate segment UUID as required by LCP
                    seg_id = str(uuid.uuid4())
                    seg_start = char_offset

                    # Process tokens using token delimiters
                    token_delimiters = r"[', ]"
                    tokens = [t for t in re.split(token_delimiters, segment) if t]

                    for token in tokens:
                        # Get or create form ID
                        form_id = form_dict.get(token, len(form_dict) + 1)
                        if token not in form_dict:
                            form_dict[token] = form_id
                            # Write to lookup tables
                            form_csv.writerow([form_id, token])
                            lemma_csv.writerow([form_id, token])

                        # Write token with character range
                        token_range = f"[{char_offset},{char_offset + len(token)})"
                        tok_csv.writerow(
                            [
                                token_id,
                                form_id,
                                form_id,  # Same ID for lemma
                                token_range,
                                seg_id,
                            ]
                        )

                        # Update counters
                        char_offset += len(token) + 1  # +1 for space between tokens
                        token_id += 1

                    # Adjust segment end offset (remove trailing space)
                    seg_end = char_offset - 1

                    # Write segment
                    seg_csv.writerow([seg_id, f"[{seg_start},{seg_end})"])

                    # Add a newline character after segment
                    char_offset += 1

                # Process annotations for this document
                for annotation in annotations:
                    ann_type = annotation["type"]

                    # Skip annotation types we don't handle
                    if ann_type not in annotation_types:
                        continue

                    start_offset = annotation["start_offset"]
                    end_offset = annotation["end_offset"]
                    annotation_text = annotation["text"]
                    check_cond = text[start_offset:end_offset] == annotation_text
                    if not check_cond:
                        # sometimes the text in the file is not the same as
                        # the annotation text because of the newline character
                        check_cond = (
                            text.replace("\n", "")[start_offset:end_offset]
                            == annotation_text
                        )
                        if not check_cond:
                            continue

                    # Get the annotation ID
                    ann_id = annotation_counters[ann_type]
                    annotation_counters[ann_type] += 1

                    # Create character range
                    char_range = f"[{start_offset},{end_offset})"

                    if ann_type == "placeName":
                        full_ref = annotation.get("attributes", {}).get("ref", "")
                        text = annotation.get("text", "")

                        # Extract base name (part before the first period)
                        base_ref = (
                            full_ref.split(".")[0] if "." in full_ref else full_ref
                        )

                        # Check if this base ref already exists
                        if base_ref in attribute_lookups["placeName_ref"]:
                            ref_id = attribute_lookups["placeName_ref"][base_ref][
                                0
                            ]  # Get the ID
                        else:
                            ref_id = len(attribute_lookups["placeName_ref"]) + 1
                            attribute_lookups["placeName_ref"][base_ref] = (
                                ref_id,
                                text,
                            )  # Store both ID and text

                        annotation_writers[ann_type].writerow(
                            [ann_id, char_range, ref_id]
                        )

                    # elif ann_type == "hi":
                    #     rend = annotation.get("attributes", {}).get("rend", "")
                    #     annotation_writers[ann_type].writerow(
                    #         [ann_id, char_range, rend]
                    #     )

                    elif ann_type == "choice":
                        alt_text = annotation.get("alternative_text", "")
                        alt_id = attribute_lookups["choice_alternative"].get(
                            alt_text, len(attribute_lookups["choice_alternative"]) + 1
                        )
                        attribute_lookups["choice_alternative"][alt_text] = alt_id
                        annotation_writers[ann_type].writerow(
                            [ann_id, char_range, alt_id]
                        )

                    elif ann_type == "origDate":
                        when = annotation.get("attributes", {}).get("when", "")
                        annotation_writers[ann_type].writerow(
                            [ann_id, char_range, when]
                        )

                    # elif ann_type == "term":
                    #     ref = annotation.get("attributes", {}).get("ref", "")
                    #     ref_id = attribute_lookups["term_ref"].get(
                    #         ref, len(attribute_lookups["term_ref"]) + 1
                    #     )
                    #     attribute_lookups["term_ref"][ref] = ref_id
                    #     annotation_writers[ann_type].writerow(
                    #         [ann_id, char_range, ref_id]
                    #     )

                    elif ann_type == "persName":
                        full_ref = annotation.get("attributes", {}).get("ref", "")
                        text = annotation.get("text", "")

                        # Extract base name (part before the first period)
                        base_ref = (
                            full_ref.split(".")[0] if "." in full_ref else full_ref
                        )

                        if base_ref in attribute_lookups["persName_ref"]:
                            ref_id = attribute_lookups["persName_ref"][base_ref][0]
                        else:
                            ref_id = len(attribute_lookups["persName_ref"]) + 1
                            attribute_lookups["persName_ref"][base_ref] = (ref_id, text)

                        annotation_writers[ann_type].writerow(
                            [ann_id, char_range, ref_id]
                        )

                    elif ann_type == "substitution":
                        alt_text = annotation.get("alternative_text", "")
                        alt_id = attribute_lookups["substitution_alternative"].get(
                            alt_text,
                            len(attribute_lookups["substitution_alternative"]) + 1,
                        )
                        attribute_lookups["substitution_alternative"][alt_text] = alt_id
                        annotation_writers[ann_type].writerow(
                            [ann_id, char_range, alt_id]
                        )

                    # elif ann_type == "note":
                    #     note_text = annotation.get("text", "")
                    #     text_id = attribute_lookups["note_text"].get(
                    #         note_text, len(attribute_lookups["note_text"]) + 1
                    #     )
                    #     attribute_lookups["note_text"][note_text] = text_id
                    #     annotation_writers[ann_type].writerow(
                    #         [ann_id, char_range, text_id]
                    #     )

                    elif ann_type == "date":
                        dur_iso = annotation.get("attributes", {}).get("dur-iso", "")
                        annotation_writers[ann_type].writerow(
                            [ann_id, char_range, dur_iso]
                        )

                # Write document with title from metadata
                editors_lst = metadata.get("editors", [])
                if editors_lst:
                    editors_str = ", ".join(editors_lst)
                else:
                    editors_str = ""
                doc_csv.writerow(
                    [
                        document_id,
                        f"[{doc_start},{char_offset - 1})",
                        metadata.get("title", ""),
                        editors_str,
                        metadata.get("date_electronic", ""),
                        metadata.get("date_print", ""),
                        metadata.get("origDate_when", ""),
                        subdir_name,
                    ]
                )

                # Update document counter
                document_id += 1

        # Close annotation files
        for file in annotation_files.values():
            file.close()

        # Write lookup table data
        for key, lookup_dict in attribute_lookups.items():
            if key in lookup_writers:
                if key in ["placeName_ref", "persName_ref"]:
                    for value, (value_id, text) in lookup_dict.items():
                        lookup_writers[key].writerow([value_id, value, text])
                else:
                    for value, value_id in lookup_dict.items():
                        lookup_writers[key].writerow([value_id, value])

        # Close lookup files
        for file in lookup_files.values():
            file.close()

    # Create LCP configuration file
    config = {
        "meta": {
            "name": "SSRQ TEI Corpus",
            "author": "SSRQ Conversion Script",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "version": 1,
            "corpusDescription": "Converted SSRQ TEI documents",
        },
        "firstClass": {"document": "Document", "segment": "Segment", "token": "Token"},
        "layer": {
            "Document": {
                "layerType": "span",
                "contains": "Segment",
                "attributes": {
                    "title": {"type": "text", "nullable": True},
                    "editors": {"type": "text", "nullable": True},
                    "date_electronic": {"type": "text", "nullable": True},
                    "date_print": {"type": "text", "nullable": True},
                    "orig_date": {"type": "text", "nullable": True},
                    "canton": {
                        "type": "categorical",
                        "values": list(xml_files_by_subdir.keys()),
                        "nullable": True,
                    },
                },
            },
            "Segment": {
                "layerType": "span",
                "contains": "Token",
                "attributes": {},
            },
            "Token": {
                "layerType": "unit",
                "anchoring": {"stream": True, "time": False, "location": False},
                "attributes": {
                    "form": {"type": "text", "nullable": False},
                    "lemma": {"type": "text", "nullable": False},
                },
            },
            "PlaceName": {
                "layerType": "span",
                "anchoring": {"stream": True, "time": False, "location": False},
                "attributes": {
                    "ref": {"type": "text", "nullable": False},
                },
            },
            "Choice": {
                "layerType": "span",
                "anchoring": {"stream": True, "time": False, "location": False},
                "attributes": {
                    "alternative": {"type": "text", "nullable": True},
                },
            },
            "OrigDate": {
                "layerType": "span",
                "anchoring": {"stream": True, "time": False, "location": False},
                "attributes": {
                    "when": {"type": "text", "nullable": True},
                },
            },
            # "Term": {
            #     "layerType": "span",
            #     "anchoring": {"stream": True, "time": False, "location": False},
            #     "attributes": {
            #         "ref": {"type": "text", "nullable": True},
            #     },
            # },
            "PersName": {
                "layerType": "span",
                "anchoring": {"stream": True, "time": False, "location": False},
                "attributes": {
                    "ref": {"type": "text", "nullable": False},
                },
            },
            "Substitution": {
                "layerType": "span",
                "anchoring": {"stream": True, "time": False, "location": False},
                "attributes": {
                    "alternative": {"type": "text", "nullable": True},
                },
            },
            "Date": {
                "layerType": "span",
                "anchoring": {"stream": True, "time": False, "location": False},
                "attributes": {
                    "dur_iso": {"type": "text", "nullable": True},
                },
            },
        },
    }

    with open(os.path.join(output_dir, "meta.json"), "w") as json_file:
        json.dump(config, json_file, indent=4)

    return {
        "documents": document_id - 1,
        "tokens": token_id - 1,
        "forms": len(form_dict),
        "annotations": sum(annotation_counters.values()) - len(annotation_counters),
    }


corpus = process_corpus(xml_files_by_subdir)
lcp_corpus = convert_to_lcp(corpus)
