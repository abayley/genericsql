import os
import tempfile
import sublime
import sublime_plugin
import subprocess
import functools
import traceback
import time


# https://www.sublimetext.com/docs/3/api_reference.html

"""
Use the build system to invoke an external command, and put the output in a new window.
http://docs.sublimetext.info/en/latest/reference/build_systems/configuration.html

We use the build system so that we can interactively choose which command
(i.e. which database) to run from the list of build system variants.

We inherit from sublime_plugin.WindowCommand, so that the class SqlExecCommand
automatically becomes the command "sql_exec" (the run() method is invoked).
This new command then referenced in the .sublime-build file as the target e.g.
{
    // selector only works when build system is Automatic
    "selector": "source.sql",
    "target": "sql_exec",
    "cmd" : "",
    "variants": [
        { "name": "Run", "cmd" : "reset" },
        { "name": "user@database",        "dialect": "oracle",   "cmd": ["echo", "sqlplus", "-s", "user/password@host.name:1521/database", "@"] },
        { "name": "user@database.host",   "dialect": "postgres", "cmd": ["echo", "psql", "host=host.name user=user password=password dbname=database", "-f"] },
        { "name": "user@database.host",   "dialect": "mysql",    "cmd": ["echo", "mysql", "-B", "-h", "host.name", "-P", "3306", "-u", "user", "-ppassword", "-D", "database", "-e source "] }
    ]
}

The default command is an empty string, when this is passed to run() in kwargs.
When empty this will cause the Command Palette overlay to appear, and the user will choose
one of the build variants.
This will invoke run(*) again, with "dialect" and "cmd" args set from the chosen build variant.

We also have some key-bindings that invoke sql_exec directly with different args:
[
    {"keys": ["ctrl+f7"],       "command": "sql_exec", "args" : {"kill" : "True"} },
    {"keys": ["shift+f7"],      "command": "sql_exec", "args" : {"sqlscope" : "file", "action" : "reset"} },
    {"keys": ["f8"],            "command": "sql_exec", "args" : {"sqlscope" : "statement"} },
    {"keys": ["ctrl+f8"],       "command": "sql_exec", "args" : {"kill" : "True"} },
    {"keys": ["ctrl+shift+f8"], "command": "sql_exec", "args" : {"sqlscope" : "statement", "action" : "explain"} },
    {"keys": ["shift+f8"],      "command": "sql_exec", "args" : {"sqlscope" : "statement", "action" : "reset"} }
]

In each of these cases "dialect" and "cmd" are not set. Here is what happens for each case:
 - f8: only sqlscope is passed. If previous command is saved on view then reuse, otherwise show Command Palette.
 - shift+f8: causes view state to be cleared, forcing display of Command Palette.
 - ctrl+shift+f8: same as f8, but adds action=explain
 - shift+f7: same as shitft+f8, but sets scope to entire view (file)
 - ctrl+f7/ctrl+f8: kill the current execution

If you get "OSError: [Errno 8] Exec format error" then you need to add #! to your command scripts:
https://stackoverflow.com/questions/27606653/oserror-errno-8-exec-format-error


Setting up build commands:
Probably best to create a script in your personal bin which runs the command
(so you can easily reuse from terminal etc) e.g.

{
    "selector": "source.sql",
    "target": "sql_exec",
    "cmd" : "",
    "variants": [
        { "name": "Run", "cmd" : "reset" },
        { "name": "user@database",        "dialect": "oracle",   "cmd": ["oradb1"] },
        { "name": "user@database.host",   "dialect": "postgres", "cmd": ["pgdb1"] },
        { "name": "user@database.host",   "dialect": "mysql",    "cmd": ["mydb1"] }
    ]
}

where the scripts contain commands like so:

[mydb1]
#!/bin/sh
mysql -A -t -h fqdn.server.name -P 3306 -u username -ppassword -D dbname "$*"

[oradb1]  # note rlwrap sexy
#!/bin/sh
rlwrap sqlplus -s username/password@\"fqdn.server.name:1521/orasid\" $*

[pgdb1]
psql "host=fqdn.server.name user=username password=password dbname=dbname" $*
"""

# If you want to manipulate the text in a view, the best way now (ST3) is to
# create a custom TextCommand, which will give you the Edit object.
# http://www.sublimetext.com/docs/3/porting_guide.html
# https://github.com/tednaleid/sublime-EasyMotion/issues/26
class AppendTextCommand(sublime_plugin.TextCommand):
    def run(self, edit, text):
        self.view.insert(edit, self.view.size(), text)


class ClearViewCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.view.erase(edit, sublime.Region(0, self.view.size()))


class SqlExecCommand(sublime_plugin.WindowCommand):

    def log(self, text, panel_name="sql"):
        return  # suppress logging
        # get_output_panel doesn't "get" the panel, it *creates* it,
        # so we should only call get_output_panel once
        if not hasattr(self, "output_panel"):
            self.output_panel = self.window.get_output_panel(panel_name)
        v = self.output_panel

        # Write this text to the output panel and display it
        v.run_command("append", {"characters" : text + '\n'})
        v.show(v.size())
        self.window.run_command("show_panel", {"panel": "output." + panel_name})

    def reset_log(self, panel_name="sql"):
        """
        Replace the output panel with a new one.
        """
        self.output_panel = self.window.get_output_panel("sql")

    def find_preceding_newline(self, view, region):
        # search backwards to blank line (two newlines)
        i = region.a - 2
        while i > 0:
            if view.substr(sublime.Region(i, i + 2)) == "\n\n":
                return i + 2
            i -= 1
        return 0

    def find_next_newline(self, view, region):
        # search forwards to blank line (two newlines)
        i = region.a - 1
        while i < view.size():
            if view.substr(sublime.Region(i, i + 2)) == "\n\n":
                return i
            i += 1
        return view.size()

    def select_current_statement(self, view):
        sel = view.sel()
        start = self.find_preceding_newline(view, sel[0])
        end = self.find_next_newline(view, sel[0])
        view.sel().clear()
        view.sel().add(sublime.Region(start, end))

    def append_text(self, line):
        if not hasattr(self, "output_view"):
            self.log("append_text: output_view not set")
            return

        view = self.output_view
        scroll_to_end = (len(view.sel()) == 1 and view.sel()[0] == sublime.Region(view.size()))
        # Normalize newlines, Sublime Text always uses a single \n separator in memory.
        line = line.replace('\r\n', '\n').replace('\r', '\n')
        # This can be slow for large output
        view.run_command("append_text", {"text" : line})
        if scroll_to_end:
            view.show(view.size())

    def kill_shell(self):
        if hasattr(self, "shell_process"):
            if self.shell_process:
                self.log("kill_shell: call terminate on %s" % str(self.shell_process))
                self.shell_process.terminate()
            else:
                self.log("kill_shell: no process to kill")

    def shell_command(self, cmd):
        try:
            popen = subprocess.Popen(cmd, bufsize=0, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            self.shell_process = popen
            popen.poll()
            self.log("shell_command: while")
            while popen.returncode is None:
                popen.poll()
                self.log("shell_command: popen.poll %s  at %s" % (popen.returncode, time.time()))
                # popen.communicate does not return/produce output until process terminates.
                # (might be a shell buffering thing). So instead use readline to get output so far.
                try:
                    (out, _) = popen.communicate(timeout=0.20)
                    self.append_text(out)
                except subprocess.TimeoutExpired:
                    self.log("shell_command: communicate timeout, use readlines  at %s" % time.time())
                    # out = popen.stdout.readlines()
                    # self.log("shell_command: readlines append  at %s" % time.time())
                    # self.append_text("".join(out))
            # get remaining output
            try:
                self.log("shell_command: returncode not None, get remaining output with popen.communicate")
                (out, _) = popen.communicate()
                self.log("shell_command: append output")
                self.append_text(out)
            except ValueError:  # output handle closed causes this
                pass
            self.append_text("\n[Return code: %s]\n" % popen.returncode)
            popen.stdout.close()
            self.shell_process = None

        except subprocess.CalledProcessError as e:
            self.append_text(str(e))

        except OSError as e:
            if e.errno == 2:
                self.append_text("Command not found: %s" % str(cmd))
            elif e.errno == 8:
                self.append_text(str(e))
                self.append_text("\n(if you are executing a script, ensure the first line has a she-bang #!)\n")
            else:
                self.append_text(str(e))

        except Exception as e:
            self.append_text(traceback.format_exc())
            self.kill_shell()

    def find_output_view(self, view_name):
        for view in self.window.views():
            if view.name() == view_name:
                return view
        return None

    # http://www.sublimetext.com/docs/3/porting_guide.html
    # https://stackoverflow.com/questions/1180606/using-subprocess-popen-for-process-with-large-output
    # https://stackoverflow.com/questions/4417546/constantly-print-subprocess-output-while-process-is-running
    def run_command(self, cmd, syntax="Packages/SQL/SQL.sublime-syntax", working_dir=None, file_name=None, file_regex=None, line_regex=None):
        """
        Execute a command and capture output into a new view.
        """
        # try to re-use existing view, if it exists
        view_name = "SQL Output"
        if file_name:
            view_name += ": " + file_name

        view = self.find_output_view(view_name)
        if view is None:
            view = self.window.new_file()

        view.run_command("clear_view")
        self.output_view = view
        self.window.focus_view(view)
        view.show(view.size())  # scroll to end
        view.set_scratch(True)
        view.set_name(view_name)

        view.set_syntax_file(syntax)

        # Default the to the current files directory if no working directory was given
        if (working_dir == "" and self.window.active_view() and self.file):
            working_dir = os.path.dirname(self.file)
        view.settings().set("result_base_dir", working_dir)

        if file_name:
            # requires that self.output_view is already set
            self.append_text("Filename: " + file_name + "\n")
        if file_regex:
            view.settings().set("result_file_regex", file_regex)
        if line_regex:
            view.settings().set("result_line_regex", line_regex)

        # Use set_timeout_async to run async - in another thread
        # https://forum.sublimetext.com/t/execute-external-program-in-background-non-blocking-thread/11122/6
        sublime.set_timeout_async(functools.partial(self.shell_command, cmd), 0)

    def run_file(self, view, cmd, file_name, **kwargs):
        """
        Execute the SQL in the given file.
        """
        dialect = kwargs.pop("dialect")
        # Add filename to command args. Some clients will work with stdin redirection,
        # others require a specific file argument.
        # oracle and mysql are OK with stdin redirect.
        # TODO test and rationalise this code (to add input filename)
        append = ["<" + file_name]
        if dialect == "mysql":
            append = ["-e source " + file_name]
        if dialect == "postgres":
            append = ["-f" + file_name]
        if dialect == "oracle":
            append = [file_name]
        cmd += append
        # Setting/passing these regexes allow ST to parse error messages and
        # take you to the source file if you double-click on the,.
        file_regex = "^Filename: (.+)$"
        line_regex = "^\\(.+?/([0-9]+):([0-9]+)\\) [0-9]+:[0-9]+ (.+)$"
        syntax = "Packages/SQL/SQL.sublime-syntax"

        # long-running command, useful for performance testing
        # cmd = ["locate", "a"]
        self.run_command(cmd, syntax=syntax, file_name=view.file_name(), file_regex=file_regex, line_regex=line_regex)

    def write_selection_to_handle(self, view, handle):
        """
        Emit the current selection to the given file handle.
        """
        for region in view.sel():
            os_write(handle, view.substr(region))

    def run_selection(self, view, cmd, **kwargs):
        """
        Execute a piece of selected text by copying it into a temp file,
        and then sending this to sqlplus.
        """
        # Cannot use "with tempfile.NamedTemporaryFile as temp" because command
        # is invoked async i.e. after call returns. File is deleted too soon.
        # Could pass delete=False but then what advantage would it have?
        (handle, temp_file_name) = tempfile.mkstemp(suffix=".sql", text=True)
        self.write_selection_to_handle(view, handle)
        os.close(handle)
        self.run_file(view, cmd, temp_file_name, **kwargs)

    def explain_plan(self, view, cmd, **kwargs):
        """
        Copy selected text into into a temp file,
        wrap with explain plan magic,
        and then sending this to sqlplus.
        """
        (handle, temp_file_name) = tempfile.mkstemp(suffix=".sql", text=True)
        if kwargs.get("dialect") == "oracle":
            # set autotrace on
            os_write(handle, "explain plan for ")
            self.write_selection_to_handle(view, handle)
            os_write(handle, "\nset heading off")
            os_write(handle, "\nselect * from table(dbms_xplan.display);")
        else:
            os_write(handle, "explain ")
            self.write_selection_to_handle(view, handle)
        os.close(handle)
        self.run_file(view, cmd, temp_file_name, **kwargs)

    def run(self, **kwargs):
        """
        This command has quite a complex little set of states.
        The idea is that you use:
          f7 (or ctrl+b): to run the entire file
          f8: to run just the current statement (whatever text block the cursor
              happens to be in, delimited by blank lines)
          shift+f7 (or ctrl+shift+b): to run the entire file, but first prompt for DB connection
          shift+f8: to run the current statement, but first prompt for DB connection

        If there is an existing selection, all commands
        (f7, f8, shift+f7, etc) will run just the selection.

        There are two arguments that can appear in kwargs that help manage the states:
            sqlscope: file (f7), statement (f8)
            action: reset (shift+f7/f8), explain (ctrl+shift+f8), execute (f7/f8)
        """
        self.reset_log()
        self.log("---------- run start")
        for (k, v) in kwargs.items():
            self.log(str(k) + " : " + str(v))
        self.log("----------")

        if kwargs.get("kill", False):
            self.kill_shell()
            return

        view = self.window.active_view()
        action = kwargs.pop("action", "")
        # If user pressed shift then erase stored previous command.
        # This will force popup prompt for candidate commands.
        if action == "reset":
            view.settings().erase("cmd")

        # If scope was passed in then save it on view.
        # If scope is saved on view then use it (only if not passed in).
        sqlscope = kwargs.pop("sqlscope", "")
        if not sqlscope:
            sqlscope = view.settings().get("sqlscope", "file")
        else:
            view.settings().set("sqlscope", sqlscope)

        # Check if cmd is already set on view.
        # If not, then open the overlay window to prompt the user for the build command to run.
        # The overlay will call this method with new params.
        dialect = kwargs.get("dialect", "")
        cmd = kwargs.pop("cmd", "")
        if not cmd:
            cmd = view.settings().get("cmd", "")
            dialect = view.settings().get("dialect", "")
            if not cmd:
                self.window.run_command("show_overlay", {"overlay": "command_palette", "text": "Build: " + kwargs.get("prefix", "")})
                return

        # Remove scope from view after checking CMD.
        # This lets us invoke F8 on a new buffer,
        # have it prompt for build config, and then run statement scope
        # when re-entered.
        sqlscope = view.settings().get("sqlscope")
        view.settings().erase("sqlscope")

        # Save cmd and dialect against view so subseqeuent runs can reuse it
        # (press shift+F7 or ctrl+shift+b to reset).
        view.settings().set("cmd", cmd)
        view.settings().set("dialect", dialect)
        kwargs["dialect"] = dialect

        # If there is a selection, use it regardless of scope.
        # If there is no selection and scope is statement then
        # make a selection and run it.
        sel = view.sel()
        empty_selection = len(sel) == 1 and sel[0].a == sel[0].b
        if empty_selection and sqlscope == "statement":
            self.select_current_statement(view)
            empty_selection = False

        if not empty_selection:
            if action == "explain":
                self.explain_plan(view, cmd, **kwargs)
            else:
                self.run_selection(view, cmd, **kwargs)
        else:
            # Just run entire file
            self.run_file(view, cmd, view.file_name(), **kwargs)


def os_write(handle, s):
    os.write(handle, bytes(s, "UTF-8"))
