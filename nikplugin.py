#!/usr/bin/env python3

"""
Version:
3.0 Make plugin compatible with Gimp 3.x
    - Revise the code using v3.x API
    - Refactor code and format with black
"""

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gegl", "0.4")

from gi.repository import (
    GLib,
    GObject,
    Gegl,
    Gimp,
    GimpUi,
)

from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

import os
import shlex
import subprocess
import sys
import tempfile
import traceback

# Define plug-in metadata
PROC_NAME = "NikCollection"
HELP = "Call an external program"
DOC = "Call an external program passing the active layer as a temp file"
AUTHOR = "nemo"
COPYRIGHT = "GNU General Public License v3"
DATE = "2025-03-25"


def list_progs(idx: Optional[int] = None) -> Union[List[str], Tuple[str, Path, str]]:
    """
    Build a list of Nik programs installed on the system

    Args:
        idx: Optional index of the program to return details for

    Returns:
        If idx is None, returns a list of program names
        Otherwise, returns [prog_name, prog_filepath, output_ext] for the specified program
    """

    # NOTE: Update this base path to match your installation
    # Assume all nik programs are installed in the same base directory
    install_path = Path("C:/Program Files/Google/Nik Collection")

    # Define program details as: (program_name, executable_filename, file_extension)
    progs_info = [
        ("Analog Efex Pro 2", "Analog Efex Pro 2.exe", "jpg"),
        ("Color Efex Pro 4", "Color Efex Pro 4.exe", "jpg"),
        ("DFine 2", "Dfine2.exe", "png"),
        ("HDR Efex Pro 2", "HDR Efex Pro 2.exe", "jpg"),
        ("Sharpener Pro 3", "SHP3OS.exe", "png"),
        ("Silver Efex Pro 2", "Silver Efex Pro 2.exe", "jpg"),
        ("Viveza 2", "Viveza 2.exe", "png"),
    ]

    # Build the list of existing programs
    progs_lst = []
    for prog, exe, ext in progs_info:
        fullpath = install_path / prog / exe
        if fullpath.exists():
            progs_lst.append((prog, fullpath, ext))

    if idx is None:
        return [prog[0] for prog in progs_lst]
    else:
        return progs_lst[idx]


def plugin_main(
    procedure: Gimp.Procedure,
    run_mode: Gimp.RunMode,
    image: Gimp.Image,
    drawables: List[Gimp.Drawable],
    config: Gimp.ProcedureConfig,
    data: Any,
) -> Gimp.ValueArray:
    """
    Main function executed by the plugin. Call an external Nik Collection program on the active layer
    It supports two modes:
      - When visible == 0, operates on the active drawable (current layer).
      - When visible != 0, creates a new layer from the composite of all visible layers

    Workflow:
      - Show dialog in interactive mode for setting parameters (layer source and external program)
      - Start an undo group (let user undo all operations as a single step)
      - Copy and save the layer to a temporary file based on the "visible" setting
      - Call the chosen external Nik Collection program
      - Load the modified result as a new layer and paste it
      - Restore any saved selection and clean up temporary resources
      - End the undo group and finalize
    """

    # Show dialog in interactive mode
    if run_mode == Gimp.RunMode.INTERACTIVE:
        GimpUi.init(PROC_NAME)
        Gegl.init(None)
        dialog = GimpUi.ProcedureDialog(procedure=procedure, config=config)
        dialog.fill(None)
        if not dialog.run():
            dialog.destroy()
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        dialog.destroy()

    # Get parameters
    visible = config.get_property("visible")
    command_idx = config.get_property("command")

    # Check if drawables is empty
    if not drawables or len(drawables) == 0:
        return procedure.new_return_values(
            Gimp.PDBStatusType.CALLING_ERROR,
            GLib.Error().new_literal(
                Gimp.PlugIn.error_quark(), "No drawable provided", 0
            ),
        )
    drawable = drawables[0]

    # Start an undo group
    Gimp.context_push()
    image.undo_group_start()

    # Copy so the save operations doesn't affect the original
    if visible == 0:
        # Use the active drawable
        temp = drawable
    else:
        # Get the current visible
        temp = Gimp.Layer.new_from_visible(image, image, "Visible")
        image.insert_layer(temp, None, 0)

    # Copy the layer content
    buffer = Gimp.edit_named_copy([temp], "ShellOutTemp")

    # Save selection if one exists
    hassel = not Gimp.Selection.is_empty(image)
    if hassel:
        savedsel = Gimp.Selection.save(image)

    # Create a new image with the copied content
    tempimage = Gimp.edit_named_paste_as_new_image(buffer)
    Gimp.Buffer.delete(buffer)
    if not tempimage:
        image.undo_group_end()
        Gimp.context_pop()
        return procedure.new_return_values(
            Gimp.PDBStatusType.EXECUTION_ERROR,
            GLib.Error(),
        )

    Gimp.Image.undo_disable(tempimage)

    # Get the active layer from the temp image
    tempdrawable = Gimp.Image.get_active_layer(tempimage)

    # Get the program to run and filetype
    progtorun = list_progs(command_idx)

    # Use temp file names from gimp, it reflects the user's choices in gimp.rc
    # change as indicated if you always want to use the same temp file name
    # tempfilename = pdb.gimp_temp_name(progtorun[2])
    tempfilename = os.path.join(
        tempfile.gettempdir(), "ShellOutTempFile." + progtorun[2]
    )

    # !!! Note no run-mode first parameter, and user entered filename is empty string
    Gimp.progress_init("Saving a copy")
    Gimp.file_save(
        Gimp.RunMode.NONINTERACTIVE,
        tempimage,
        tempdrawable,
        GLib.file_new_for_path(tempfilename),
    )

    # Build command line call
    command = progtorun[1] + ' "' + tempfilename + '"'
    args = shlex.split(command)

    # Invoke external command
    Gimp.progress_init("Calling " + progtorun[0] + "...")
    Gimp.progress_pulse()
    child = subprocess.Popen(args, shell=False)
    child.communicate()

    # Put it as a new layer in the opened image
    try:
        newlayer2 = Gimp.file_load_layer(
            Gimp.RunMode.NONINTERACTIVE,
            tempimage,
            GLib.file_new_for_path(tempfilename),
        )
    except Exception as e:
        print(f"Error loading file: {e}")
        image.undo_group_end()
        Gimp.context_pop()
        return procedure.new_return_values(
            Gimp.PDBStatusType.EXECUTION_ERROR,
            GLib.Error(),
        )

    tempimage.insert_layer(newlayer2, None, -1)
    buffer = Gimp.edit_named_copy([newlayer2], "ShellOutTemp")

    if visible == 0:
        Gimp.Item.resize(drawable, newlayer2.get_width(), newlayer2.get_height(), 0, 0)
        sel = Gimp.edit_named_paste(drawable, buffer, True)
        Gimp.Item.transform_translate(
            drawable,
            (tempdrawable.get_width() - newlayer2.get_width()) / 2,
            (tempdrawable.get_height() - newlayer2.get_height()) / 2,
        )
    else:
        Gimp.Item.resize(temp, newlayer2.get_width(), newlayer2.get_height(), 0, 0)
        sel = Gimp.edit_named_paste(temp, buffer, True)
        Gimp.Item.transform_translate(
            temp,
            (tempdrawable.get_width() - newlayer2.get_width()) / 2,
            (tempdrawable.get_height() - newlayer2.get_height()) / 2,
        )

    Gimp.Buffer.delete(buffer)
    Gimp.edit_clear([temp])
    Gimp.floating_sel_anchor(sel)

    # Load up old selection
    if hassel:
        Gimp.Selection.load(savedsel)
        image.remove_channel(savedsel)

    # Cleanup
    os.remove(tempfilename)  # delete the temporary file
    tempimage.delete()  # delete the temporary image

    # End the undo group
    image.undo_group_end()
    Gimp.displays_flush()
    Gimp.context_pop()

    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


class NikPlugin(Gimp.PlugIn):

    def do_query_procedures(self):
        return [PROC_NAME]

    def do_create_procedure(self, name):
        procedure = Gimp.ImageProcedure.new(
            self,
            name,
            Gimp.PDBProcType.PLUGIN,
            plugin_main,
            None,
        )

        procedure.set_image_types("RGB*, GRAY*")
        procedure.set_attribution(AUTHOR, COPYRIGHT, DATE)
        procedure.set_documentation(HELP, DOC, None)
        procedure.set_menu_label(PROC_NAME)
        procedure.add_menu_path("<Image>/Filters/")

        # Replace PF_RADIO choice
        visible_choice = Gimp.Choice.new()
        visible_choice.add("new_from_visible", 1, "new from visible", "new layer")
        visible_choice.add("current_layer", 0, "use current layer", "current layer")
        procedure.add_choice_argument(
            "visible",
            "Layer:",
            "Choose layer source",
            visible_choice,
            "new_from_visible",
            GObject.ParamFlags.READWRITE,
        )

        # Dropdown selection list of programs
        command_choice = Gimp.Choice.new()
        programs = list_progs()
        for idx, prog in enumerate(programs):
            command_choice.add(prog, idx, prog, prog)
        procedure.add_choice_argument(
            "command",
            "Program:",
            "Select external program to run",
            command_choice,
            programs[0],
            GObject.ParamFlags.READWRITE,
        )
        return procedure


Gimp.main(NikPlugin.__gtype__, sys.argv)
