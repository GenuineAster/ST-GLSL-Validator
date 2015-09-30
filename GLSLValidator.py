import sublime
import sublime_plugin
import re
import subprocess
import os


class GLShaderError:
    """ Represents an error """
    region = None
    message = ''

    def __init__(self, region, message):
        self.region = region
        self.message = message


class glslangValidatorCommandLine:
    """ Wrapper for glslangValidator CLI """

    packagePath = "GLSL Validator"
    platform = sublime.platform()
    errorPattern = re.compile("ERROR: 0:(\d+): '([^\']*)' : (.*)")
    permissionChecked = False
    glslangValidatorPath = {
        "osx": "./glslangValidatorLinux",
        "linux": "./glslangValidatorLinux",
        "windows": "glslangValidatorWindows.exe"
    }

    def ensure_script_permissions(self):
        """ Ensures that we have permission to execute the command """

        if not self.permissionChecked:
            os.chmod(sublime.packages_path() + os.sep + self.packagePath + os.sep + self.glslangValidatorPath[self.platform], 0o755)

        self.permissionChecked = True
        return self.permissionChecked

    def validate_contents(self, view):
        """ Validates the file contents using glslangValidator """
        glslangValidatorPath = self.glslangValidatorPath[self.platform]
        errors = []
        fileLines = view.lines(
            sublime.Region(0, view.size())
        )

        specCmd = ''

        # Create a shell process for essl_to_glsl and pick
        # up its output directly
        glslangValidatorProcess = subprocess.Popen(
            glslangValidatorPath + ' ' + specCmd + ' "' + view.file_name() + '"',
            cwd=sublime.packages_path() + os.sep + self.packagePath + os.sep,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=True)

        if glslangValidatorProcess.stdout is not None:
            errlines = glslangValidatorProcess.stdout.readlines()

            # Go through each error, ignoring any comments
            for e in errlines:

                e = e.decode("utf-8")

                # Check if there was a permission denied
                # error running the essl_to_glsl cmd

                if re.search("permission denied", str(e), flags=re.IGNORECASE):
                    sublime.error_message("GLSLValidator: permission denied to use glslangValidator command")
                    return []

                # ignore glslangValidator's comments
                if not re.search("^####", e):

                    # Break down the error using the regexp
                    errorDetails = self.errorPattern.match(e)

                    # For each match construct an error
                    # object to pass back
                    if errorDetails is not None:
                        errorLine = int(errorDetails.group(1)) - 1
                        errorToken = errorDetails.group(2)
                        errorDescription = errorDetails.group(3)
                        errorLocation = fileLines[errorLine]

                        # If there is a token try and locate it
                        if len(errorToken) > 0:
                            betterLocation = view.find(
                                errorToken,
                                errorLocation.begin(),
                                sublime.LITERAL)

                            # Ensure we have a match before we
                            # replace the error region
                            if betterLocation is not None and not betterLocation.empty():
                                errorLocation = betterLocation

                        errors.append(GLShaderError(
                            errorLocation,
                            errorDescription
                        ))

        return errors


class GLSlValidatorCommand(sublime_plugin.EventListener):
    """ Main Validator Class """
    glslangValidatorCLI = glslangValidatorCommandLine()
    errors = None
    loadedSettings = False
    pluginSettings = None

    # these are the default settings. They are overridden and
    # documented in the GLSlValidator.sublime-settings file
    DEFAULT_SETTINGS = {
        "glslvalidator_enabled": 1,
    }

    def __init__(self):
        """ Startup """

    def clear_settings(self):
        """ Resets the settings value so we will overwrite on the next run """
        for window in sublime.windows():
            for view in window.views():
                if view.settings().get('glslvalidator_configured') is not None:
                    view.settings().set('glslvalidator_configured', None)

    def apply_settings(self, view):
        """ Applies the settings from the settings file """

        # load in the settings file
        if self.pluginSettings is None:
            self.pluginSettings = sublime.load_settings(__name__ + ".sublime-settings")
            self.pluginSettings.clear_on_change('glslvalidator_validator')
            self.pluginSettings.add_on_change('glslvalidator_validator', self.clear_settings)

        if view.settings().get('glslvalidator_configured') is None:

            view.settings().set('glslvalidator_configured', True)

            # Go through the default settings
            for setting in self.DEFAULT_SETTINGS:

                # set the value
                settingValue = self.DEFAULT_SETTINGS[setting]

                # check if the user has overwritten the value
                # and switch to that instead
                if self.pluginSettings.get(setting) is not None:
                    settingValue = self.pluginSettings.get(setting)

                view.settings().set(setting, settingValue)

    def clear_errors(self, view):
        """ Removes any errors """
        view.erase_regions('glshadervalidate_errors')

    def is_glsl(self, view):
        """ Checks that the file is GLSL """
        syntax = view.settings().get('syntax')
        isShader = False
        if syntax is not None:
            isShader = re.search('GLSL', syntax, flags=re.IGNORECASE) is not None
        return isShader

    def is_valid_file_ending(self, view):
        """ Checks that the file ending will work for glslangValidator """
        isValidFileEnding = re.search('(frag|vert|geom|tesc|tese|comp)$', view.file_name()) is not None
        return isValidFileEnding

    def show_errors(self, view):
        """ Passes over the array of errors and adds outlines """

        # Go through the errors that came back
        errorRegions = []
        for error in self.errors:
            errorRegions.append(error.region)

        # Put an outline around each one and a dot on the line
        view.add_regions(
            'glshadervalidate_errors',
            errorRegions,
            'glshader_error',
            'dot',
            sublime.DRAW_OUTLINED
        )

    def on_selection_modified(self, view):
        """ Shows a status message for an error region """

        view.erase_status('glslvalidator')

        # If we have errors just locate
        # the first one and go with that for the status
        if self.is_glsl(view) and self.errors is not None:
            for sel in view.sel():
                for error in self.errors:
                    if error.region.contains(sel):
                        view.set_status('glslvalidator', error.message)
                        return

    def on_load(self, view):
        """ File loaded """
        self.run_validator(view)

    def on_activated(self, view):
        """ File activated """
        self.run_validator(view)

    def on_post_save(self, view):
        """ File saved """
        self.run_validator(view)

    def run_validator(self, view):
        """ Runs a validation pass """

        # clear the last run
        view.erase_status('glslvalidator')

        # set up the settings if necessary
        self.apply_settings(view)

        # early return if they have disabled the linter
        if view.settings().get('glslvalidator_enabled') == 0:
            self.clear_errors(view)
            return

        # early return for anything not syntax
        # highlighted as GLSL / ESSL
        if not self.is_glsl(view):
            return

        # glslangValidator expects files to be suffixed as .frag or
        # .vert so we need to do that check here
        if self.is_valid_file_ending(view):

            # Clear the last set of errors
            self.clear_errors

            # ensure that the script has permissions to run
            # this only runs once and is short circuited on subsequent calls
            self.glslangValidatorCLI.ensure_script_permissions()

            # Get the file and send to glslangValidator
            self.errors = self.glslangValidatorCLI.validate_contents(view)
            self.show_errors(view)
        else:
            view.set_status('glslvalidator', "File name must end in .frag or .vert")
