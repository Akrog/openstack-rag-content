#!/usr/bin/python3.12
# Copyright 2025 Red Hat, Inc.
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
"""Convert .adoc formatted RHOSO documentation to text formatted files."""

import argparse
from pathlib import Path
import logging
from packaging.version import Version
from typing import Generator, Tuple
import xml.etree.ElementTree as ET
import re
import subprocess
import tempfile

LOG = logging.getLogger()
logging.basicConfig(level=logging.INFO)

# Output file extension for converted documents
OUTPUT_FILE_EXTENSION = ".txt"

DEFAULT_EXCLUDE_TITLES = [
    "hardening_red_hat_openstack_services_on_openshift",  # Replaced by ./configuring_security_services and ./performing_security_operations
    "integrating_openstack_identity_with_external_user_management_services",  # Replaced by configuring_security_services and performing_security_operations
    "firewall_rules_for_red_hat_openstack_platform",  # Not applicable to 18+
    "managing_overcloud_observability",  # Replaced by ./customizing_the_red_hat_openstack_services_on_openshift_deployment/master.txt
    "network_planning_(sandbox)",  # Content (other than MTU details) included in ./planning_your_deployment/master.txt
    "managing_secrets_with_the_key_manager_service",  # Replaced by ./performing_security_operations/master.txt
    "migrating_to_the_ovn_mechanism_driver",  # Not applicable to 18+
    "deploying_red_hat_openstack_platform_at_scale",  # Content is just a stub (WIP)
    "deploying_distributed_compute_nodes_with_separate_heat_stacks",  # Not applicable to 18+
    "installing_ember-csi_on_openshift_container_platform",  # Not applicable to 18+
    "introduction_to_red_hat_openstack_platform",  # Not applicable to 18+
    "red_hat_openstack_platform_benchmarking_service",  # Not applicable to 18+
    "backing_up_and_restoring_the_undercloud_and_control_plane_nodes",  # No content in this doc
    "configuring_dns_as_a_service",  # WIP, expected for RHOSO 18 FR3
]

DEFAULT_REMAP_TITLES = {
    "command_line_interface_(cli)_reference": "command_line_interface_reference"
}


def get_argument_parser() -> argparse.ArgumentParser:
    """Get ArgumentParser."""
    parser = argparse.ArgumentParser(
        description="Convert RHOSO AsciiDoc formatted documentation to text format.",
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "-i",
        "--input-dir",
        required=False,
        type=Path,
    )
    input_group.add_argument(
        "-n",
        "--relnotes-dir",
        required=False,
        type=Path,
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "-v",
        "--docs-version",
        required=False,
        default="18.0",
        type=str,
    )
    parser.add_argument(
        "-a",
        "--attributes-file",
        required=False,
        type=Path,
    )
    parser.add_argument(
        "-e",
        "--exclude-titles",
        required=False,
        type=str,
        nargs="+",
        default=DEFAULT_EXCLUDE_TITLES,
    )

    parser.add_argument(
        "-r",
        "--remap-titles",
        required=False,
        type=str,
        nargs="+",
        default=DEFAULT_REMAP_TITLES,
    )

    return parser


def get_xml_element_text(root_element: ET.Element, element_name) -> str | None:
    """Get text stored in XML element."""
    element = root_element.find(element_name)
    if element is None:
        LOG.warning(f"Can not find XML element => {element_name}")
        return None

    element_text = element.text
    if element_text is None:
        LOG.warning(f"No text found inside of element => {element_name}")
        return None

    return element_text


def red_hat_docs_path(
    input_dir: Path,
    output_dir: Path,
    docs_version: str,
    exclude_list: list,
    remap_titles: list,
) -> Generator[Tuple[Path, Path], None, None]:
    """Generate input and output path for asciidoctor based converter

    This function takes a look at master.adoc formatted files and based on the information
    provided in the docinfo.xml file stored within the same directory as the master.adoc
    file it generates pair of (input_path, output_path).

    The output path matches the path of that file in the published documentation (suffix
    of the URL).

    Args:
        input_dir:
            Directory containing the .adoc formatted files (searched using master.adoc regex)
        output_dir:
            Directory where the converted .adoc file should be stored.
    """
    for file in input_dir.rglob("master.adoc"):
        metadata_file_name = "docinfo.xml"
        docinfo = file.parent.joinpath(metadata_file_name)

        if not docinfo.exists():
            LOG.warning(f"{docinfo} can not be found. Skipping ...")
            continue

        with open(docinfo, "r") as f:
            # This is needed because docinfo.xml is not properly formatted XML file
            # because it does not contain a single root tag.
            docinfo_content = f.read()
            tree = ET.fromstring(f"<root>{docinfo_content}</root>")

            productnumber = get_xml_element_text(tree, "productnumber")
            if Version(productnumber) != Version(docs_version):
                LOG.warning(
                    f"{docinfo} productnumber {productnumber} != {docs_version}. Skipping ..."
                )
                continue

            if (path_title := get_xml_element_text(tree, "title")) is None:
                LOG.warning(f"{docinfo} title is blank. Skipping ...")
                continue

            path_title = path_title.lower().replace(" ", "_")

        if path_title in exclude_list:
            LOG.info(f"{path_title} is in exclude list. Skipping ...")
            continue

        if path_title in remap_titles:
            new_path_title = remap_titles[path_title]
            LOG.info(f"Remapping {path_title} to {new_path_title}.")
            path_title = new_path_title

        yield Path(file), output_dir / path_title / f"master{OUTPUT_FILE_EXTENSION}"


def red_hat_relnotes_path(
    input_dir: Path, output_dir: Path, docs_version: str
) -> Generator[Tuple[Path, Path], None, None]:
    """Generate input and output path for asciidoctor based converter

    Args:
        input_dir:
            Directory containing the .adoc formatted files (searched for release-information docs)
        output_dir:
            Directory where the converted .adoc file should be stored.
    """
    ver_string = docs_version.replace(".", "-")
    globstring = (
        f"{ver_string}-[0-9]*/assembly_release-information-{ver_string}-[0-9]*.adoc"
    )
    for file in input_dir.rglob(globstring):
        if match := re.search(rf"{ver_string}-\d+/.*-(\d+).adoc", str(file)):
            minor_ver_string = match.group(1).replace(".", "-")
            yield (
                Path(file),
                output_dir / f"release-notes/{ver_string}-{minor_ver_string}{OUTPUT_FILE_EXTENSION}",
            )
        else:
            LOG.warning(f"Failed to detect minor_ver of {file} with regex, skipping.")


def preprocess_adoc_tables(content: str) -> str:
    """Preprocess AsciiDoc content to fix common table issues.

    Args:
        content: The raw AsciiDoc content as a string

    Returns:
        Preprocessed content with table issues fixed
    """
    lines = content.split('\n')
    new_lines = []
    in_table = False
    table_start_idx = -1
    table_lines = []

    for i, line in enumerate(lines):
        # Detect table start
        if line.startswith('|==='):
            if not in_table:
                # Starting a new table
                in_table = True
                table_start_idx = len(new_lines)
                table_lines = [line]
            else:
                # Ending a table
                table_lines.append(line)

                # Check if table has at least one body row
                # Table structure: |===, optional header row, body rows, |===
                # Body rows are those that contain | and are not the delimiters
                body_rows = [l for l in table_lines[1:-1] if l.strip() and not l.startswith('|===')]

                if len(body_rows) == 0:
                    # Empty table - add a placeholder row
                    LOG.warning(f"Found empty table at line {table_start_idx}, adding placeholder row")
                    # Insert a placeholder row before the closing |===
                    table_lines.insert(-1, '| N/A | N/A')

                new_lines.extend(table_lines)
                in_table = False
                table_lines = []
        elif in_table:
            table_lines.append(line)
        else:
            new_lines.append(line)

    # Handle case where table wasn't closed
    if in_table and table_lines:
        LOG.warning(f"Found unclosed table, closing it")
        table_lines.append('|===')
        new_lines.extend(table_lines)

    return '\n'.join(new_lines)


def find_adoc_base_dir(input_path: Path) -> Path:
    """Find the base directory for AsciiDoc includes.

    This function walks up the directory tree from the input file to find
    a suitable base directory that contains common documentation directories
    like 'assemblies', 'common', 'titles', etc.

    Args:
        input_path: Path to the input .adoc file

    Returns:
        The base directory path for resolving includes
    """
    current = input_path.parent

    # Walk up the directory tree looking for common doc directories
    for _ in range(5):  # Limit search depth to avoid going too far up
        # Check if this directory contains typical doc structure markers
        if any((current / marker).exists() for marker in ['assemblies', 'common', 'titles', 'acorns', 'manual-content']):
            return current

        # Move up one directory
        if current.parent == current:  # Reached root
            break
        current = current.parent

    # If we didn't find a suitable base directory, use the input file's parent
    # (this is the fallback for simple cases)
    return input_path.parent


def preprocess_xml_list_titles(xml_content: str) -> str:
    """Preprocess XML to convert list titles to formalpara elements.

    Pandoc doesn't preserve <itemizedlist><title> or <orderedlist><title> elements
    when converting from DocBook. This function converts them to <formalpara><title>
    elements which pandoc does convert to Div.formalpara-title.

    Args:
        xml_content: The DocBook XML content as a string

    Returns:
        Preprocessed XML with list titles converted to formalpara
    """
    import xml.etree.ElementTree as ET_

    try:
        # Parse the XML
        root = ET_.fromstring(xml_content)

        # Define the DocBook namespace
        ns = {'db': 'http://docbook.org/ns/docbook'}

        # Find all itemizedlist and orderedlist elements with title children
        for list_type in ['itemizedlist', 'orderedlist']:
            for list_elem in root.findall(f'.//{{{ns["db"]}}}{list_type}', ns):
                # Check if it has a title child
                title_elem = list_elem.find(f'{{{ns["db"]}}}title', ns)
                if title_elem is not None:
                    # Get the parent of the list
                    parent = None
                    for potential_parent in root.iter():
                        if list_elem in potential_parent:
                            parent = potential_parent
                            break

                    if parent is not None:
                        # Get the index of the list in its parent
                        list_index = list(parent).index(list_elem)

                        # Remove the title from the list
                        list_elem.remove(title_elem)

                        # Create a formalpara element with the title
                        formalpara = ET_.Element(f'{{{ns["db"]}}}formalpara')
                        # Move the title to the formalpara
                        formalpara.append(title_elem)
                        # Add an empty para as formalpara requires it
                        para = ET_.SubElement(formalpara, f'{{{ns["db"]}}}para')

                        # Insert the formalpara before the list
                        parent.insert(list_index, formalpara)

        # Convert back to string
        return ET_.tostring(root, encoding='unicode')
    except Exception as e:
        LOG.warning(f"Failed to preprocess XML list titles: {e}")
        # Return original content if preprocessing fails
        return xml_content


class RelNotesConverter:
    """Convert AsciiDoc release notes to Markdown using asciidoctor and pandoc."""
    PANDOC_FILTER_PATH = (Path(__file__).parent / "pandoc-release_notes-filter.py").absolute()
    PANDOC_LUA_FILTER_PATH = (Path(__file__).parent / "tightlists.lua").absolute()

    def __init__(self, attributes_file: Path | None = None):
        self.attributes_file = attributes_file

    def convert(self, input_path: Path, output_path: Path) -> None:
        """Convert release notes from AsciiDoc to Markdown.

        This method uses a two-step conversion process:
        1. Convert AsciiDoc to DocBook5 XML using asciidoctor
        2. Convert DocBook5 XML to Markdown using pandoc with a custom filter

        Args:
            input_path: Path to input .adoc file
            output_path: Path to output .txt (markdown) file

        Raises:
            subprocess.CalledProcessError: If asciidoctor or pandoc command fails
        """
        LOG.info("Processing: %s", str(input_path.absolute()))

        # Create output directory if it doesn't exist
        if not output_path.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            LOG.warning(
                "Destination file %s exists. It will be overwritten!",
                output_path,
            )

        # Create temporary files for the conversion process
        adoc_temp = None
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml") as xml_temp:
            try:
                xml_temp_path = Path(xml_temp.name)
                base_dir = find_adoc_base_dir(input_path)

                # If attributes file is provided, create a wrapper file with includes
                # The wrapper file must be in the base directory structure, not /tmp/
                if self.attributes_file:
                    adoc_temp = tempfile.NamedTemporaryFile(
                        mode="w",
                        suffix=".adoc",
                        dir=str(base_dir.absolute()),
                        delete=False
                    )
                    adoc_temp.write(f"include::{self.attributes_file.absolute()}[]\n\ninclude::{input_path.absolute()}[]\n")
                    adoc_temp.flush()
                    adoc_temp.close()
                    input_for_conversion = Path(adoc_temp.name)
                else:
                    input_for_conversion = input_path

                # Step 1: Convert AsciiDoc to DocBook5 XML
                asciidoctor_cmd = [
                    "asciidoctor",
                    "-b", "docbook5",
                    "-a", "fn-private=pass",
                    "--base-dir", str(base_dir.absolute()),
                    "-o", str(xml_temp_path.absolute()),
                    str(input_for_conversion.absolute()),
                ]
                subprocess.run(asciidoctor_cmd, check=True, capture_output=True)

                # Step 2: Convert DocBook5 XML to Markdown using pandoc with filter
                pandoc_cmd = [
                    "pandoc",
                    "-f", "docbook",
                    "--wrap=preserve",
                    "-t", "markdown_strict",
                    f"--filter={self.PANDOC_FILTER_PATH}",
                    f"--lua-filter={self.PANDOC_LUA_FILTER_PATH}",
                    str(xml_temp_path.absolute()),
                    "-o", str(output_path.absolute()),
                ]
                subprocess.run(pandoc_cmd, check=True, capture_output=True)

                LOG.info("Successfully converted: %s -> %s", input_path, output_path)

            except Exception as e:
                LOG.error("Failed to convert: %s -> %s (%s)", input_path, output_path, e)
                raise

            finally:
                # Clean up temporary files
                if adoc_temp and Path(adoc_temp.name).exists():
                    Path(adoc_temp.name).unlink()


class DocsConverter:
    """Convert AsciiDoc documentation to Markdown using asciidoctor and pandoc."""
    PANDOC_FILTER_PATH = (Path(__file__).parent / "pandoc-docs-filter.py").absolute()
    PANDOC_LUA_FILTER_PATH = (Path(__file__).parent / "tightlists.lua").absolute()

    def __init__(self, attributes_file: Path | None = None):
        self.attributes_file = attributes_file

    def convert(self, input_path: Path, output_path: Path) -> None:
        """Convert documentation from AsciiDoc to Markdown.

        This method uses a multi-step conversion process:
        1. Preprocess AsciiDoc to fix common table issues
        2. Convert AsciiDoc to DocBook5 XML using asciidoctor
        3. Preprocess XML to convert list titles to formalpara elements
        4. Convert DocBook5 XML to Markdown using pandoc with custom filters

        Args:
            input_path: Path to input .adoc file
            output_path: Path to output .txt (markdown) file

        Raises:
            subprocess.CalledProcessError: If asciidoctor or pandoc command fails
        """
        LOG.info("Processing: %s", str(input_path.absolute()))

        # Create output directory if it doesn't exist
        if not output_path.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            LOG.warning(
                "Destination file %s exists. It will be overwritten!",
                output_path,
            )

        # Create temporary files for the conversion process
        adoc_temp = None
        preprocessed_temp = None
        try:
            # Find base directory first, as we need it for temp file creation
            base_dir = find_adoc_base_dir(input_path)

            # Read and preprocess the input file
            with open(input_path, 'r', encoding='utf-8') as f:
                content = f.read()

            preprocessed_content = preprocess_adoc_tables(content)

            # Create temporary file with preprocessed content in the base directory
            preprocessed_temp = tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.adoc',
                delete=False,
                encoding='utf-8',
                dir=str(base_dir.absolute())
            )
            preprocessed_temp.write(preprocessed_content)
            preprocessed_temp.flush()
            preprocessed_temp.close()
            preprocessed_path = Path(preprocessed_temp.name)

            with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as xml_temp:
                xml_temp_path = Path(xml_temp.name)
                xml_temp.close()

                try:
                    # If attributes file is provided, create a wrapper file with includes
                    # The wrapper file must be in the base directory structure
                    if self.attributes_file:
                        adoc_temp = tempfile.NamedTemporaryFile(
                            mode="w",
                            suffix=".adoc",
                            delete=False,
                            encoding='utf-8',
                            dir=str(base_dir.absolute())
                        )
                        adoc_temp.write(f"include::{self.attributes_file.absolute()}[]\n\ninclude::{preprocessed_path.absolute()}[]\n")
                        adoc_temp.flush()
                        adoc_temp.close()
                        input_for_conversion = Path(adoc_temp.name)
                    else:
                        input_for_conversion = preprocessed_path

                    # Step 1: Convert AsciiDoc to DocBook5 XML
                    asciidoctor_cmd = [
                        "asciidoctor",
                        "-b", "docbook5",
                        "-a", "fn-private=pass",
                        "--base-dir", str(base_dir.absolute()),
                        "-o", str(xml_temp_path.absolute()),
                        str(input_for_conversion.absolute()),
                    ]
                    result = subprocess.run(asciidoctor_cmd, check=True, capture_output=True, text=True)
                    if result.stderr:
                        LOG.warning("asciidoctor warnings for %s:\n%s", input_path, result.stderr)

                    # Step 1.5: Preprocess XML to convert list titles to formalpara
                    with open(xml_temp_path, 'r', encoding='utf-8') as f:
                        xml_content = f.read()

                    preprocessed_xml = preprocess_xml_list_titles(xml_content)

                    with open(xml_temp_path, 'w', encoding='utf-8') as f:
                        f.write(preprocessed_xml)

                    # Step 2: Convert DocBook5 XML to Markdown using pandoc with filter
                    pandoc_cmd = [
                        "pandoc",
                        "-f", "docbook",
                        "--wrap=preserve",
                        "-t", "markdown_strict",
                        f"--filter={self.PANDOC_FILTER_PATH}",
                        f"--lua-filter={self.PANDOC_LUA_FILTER_PATH}",
                        str(xml_temp_path.absolute()),
                        "-o", str(output_path.absolute()),
                    ]
                    subprocess.run(pandoc_cmd, check=True, capture_output=True, text=True)

                    LOG.info("Successfully converted: %s -> %s", input_path, output_path)

                except subprocess.CalledProcessError as e:
                    LOG.error("Failed to convert: %s -> %s", input_path, output_path)
                    LOG.error("Command: %s", ' '.join(e.cmd))
                    LOG.error("Return code: %s", e.returncode)
                    if e.stdout:
                        LOG.error("stdout: %s", e.stdout.decode() if isinstance(e.stdout, bytes) else e.stdout)
                    if e.stderr:
                        LOG.error("stderr: %s", e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr)
                    raise

                except Exception as e:
                    LOG.error("Failed to convert: %s -> %s (%s)", input_path, output_path, e)
                    raise

                finally:
                    # Clean up temporary files
                    if xml_temp_path.exists():
                        xml_temp_path.unlink()
                    if adoc_temp and Path(adoc_temp.name).exists():
                        Path(adoc_temp.name).unlink()
                    if preprocessed_temp and preprocessed_path.exists():
                        preprocessed_path.unlink()

        except Exception as e:
            LOG.error("Failed during preprocessing: %s (%s)", input_path, e)
            raise


if __name__ == "__main__":
    parser = get_argument_parser()
    args = parser.parse_args()

    if args.input_dir:
        docs_converter = DocsConverter(attributes_file=args.attributes_file)
        for input_path, output_path in red_hat_docs_path(
            args.input_dir,
            args.output_dir,
            args.docs_version,
            args.exclude_titles,
            args.remap_titles,
        ):
            docs_converter.convert(input_path, output_path)

    if args.relnotes_dir:
        relnotes_converter = RelNotesConverter(attributes_file=args.attributes_file)
        for input_path, output_path in red_hat_relnotes_path(
            args.relnotes_dir,
            args.output_dir,
            args.docs_version,
        ):
            relnotes_converter.convert(input_path, output_path)
