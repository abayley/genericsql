# http://www.sublimetext.com/docs/2/api_reference.html
import sublime
import os.path
import exec_in_window
import tempfile


INSTALL = """
Add to Packages/GenericSQL/GenericSQL.sublime-commands
[
    {"caption": "SQL: exec file", "command": "sql_exec", "args" : {"sqlscope" : "file"} },
    {"caption": "SQL: exec stmt", "command": "sql_exec", "args" : {"sqlscope" : "statement"} },
    {"caption": "SQL: explain",   "command": "sql_exec", "args" : {"sqlscope" : "statement", "action" : "explain"} }
]

Add to Packages/GenericSQL/Default.sublime-keymap
[
    {"keys": ["f8"],            "command": "sql_exec", "args" : {"sqlscope" : "statement"} },
    {"keys": ["shift+f8"],      "command": "sql_exec", "args" : {"sqlscope" : "statement", "action" : "reset"} },
    {"keys": ["ctrl+shift+f8"], "command": "sql_exec", "args" : {"sqlscope" : "statement", "action" : "explain"} },
    {"keys": ["shift+f7"],      "command": "sql_exec", "args" : {"sqlscope" : "file", "action" : "reset"} }
]

Build file config:
{
    // selector only works when build system is Automatic
    // "selector": "source.sql",
    "target": "sql_exec",
    "cmd" : "",
    "variants": [
        { "name": "Run", "cmd" : "reset" },
        { "name": "user@database",        "dialect": "oracle",   "cmd": ["sqlplus", "-s", "user/password@host.name:1521/database", "@"] },
        { "name": "user@database.host",   "dialect": "postgres", "cmd": ["psql", "host=host.name user=user password=password dbname=database", "-f"] },
        { "name": "user@database.host",   "dialect": "mysql",    "cmd": ["mysql", "-B", "-h", "host.name", "-P", "3306", "-u", "user", "-ppassword", "-D", "database", "-e source "] }
    ]
}
"""

PSQL_NOTES = """
http://www.postgresql.org/docs/8.4/static/app-psql.html

psql equivalents to sqlplus:
@file -> \i file
prompt -> \echo or \qecho
spool -> \o
exit -> \q
host -> \!
define -> \set
&var -> :var

`cat file.txt` : runs command, substitutes output

"""

class SqlExecCommand(exec_in_window.ExecInWindowCommand):

    def log(self, text, panel_name="sql"):
        # get_output_panel doesn't "get" the panel, it *creates* it,
        # so we should only call get_output_panel once
        if not hasattr(self, 'output_view'):
            self.output_view = self.window.get_output_panel(panel_name)
        v = self.output_view
        # Write this text to the output panel and display it
        edit = v.begin_edit()
        v.insert(edit, v.size(), text + '\n')
        v.end_edit(edit)
        v.show(v.size())
        self.window.run_command("show_panel", {"panel": "output." + panel_name})

    def reset_log(self, panel_name="svn"):
        """
        Replace the output panel with a new one.
        """
        self.output_view = self.window.get_output_panel("sql")

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

    def run_file(self, view, cmd, file_name, **kwargs):
        # Remove dialect kwarg. If you don't then command execution will fail.
        kwargs.pop("dialect")
        # Suffix filename to last argument. Must be suffixed to last arg, as opposed to
        # appended to arg list, as mysql requires it.
        cmd[-1] = cmd[-1] + file_name
        # self.log("command: " + str(cmd))
        # self.log(str(view.settings()))
        # Setting/passing these regexes allow ST to parse error messages and
        # take you to the source file if you double-click on the,.
        file_regex = "^Filename: (.+)$"
        line_regex = "^\\(.+?/([0-9]+):([0-9]+)\\) [0-9]+:[0-9]+ (.+)$"
        kwargs["syntax"] = "Packages/SQL/SQL.tmLanguage"
        kwargs["filename"] = view.file_name()
        super(SqlExecCommand, self).run(cmd, file_regex, line_regex, **kwargs)

    def run_selection(self, view, cmd, **kwargs):
        """
        Execute a piece of selected text by copying it into a temp file,
        and then sending this to sqlplus.
        """
        # Cannot use "with tempfile.NamedTemporaryFile as temp" because command
        # is invoked async i.e. after call returns. File is deleted too soon.
        # Could pass delete=False but then what advantage would it have?
        (handle, temp_file_name) = tempfile.mkstemp(suffix=".sql", text=True)
        for region in view.sel():
            os.write(handle, view.substr(region))
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
            os.write(handle, "explain plan for ")
            for region in view.sel():
                os.write(handle, view.substr(region))
            os.write(handle, "\nset heading off")
            os.write(handle, "\nselect * from table(dbms_xplan.display);")
        else:
            os.write(handle, "explain ")
            for region in view.sel():
                os.write(handle, view.substr(region))
        os.close(handle)
        self.run_file(view, cmd, temp_file_name, **kwargs)

    def run(self, **kwargs):
        """
        This command has quite a complex little set of states.
        The idea is that you use:
          f7 (or ctrl+b): to run the entire file
          f8: to run just the current statement (whatever text the cursor
              happens to be in, delimited by blank lines)
          shift+f7 (or ctrl+shift+b): to run the entire file, but first prompt for DB connection
          shift+f8: to run the current statement, but first prompt for DB connection

        If there is an existing selection, all commands
        (f7, f8, shift+f7, etc) will run just the selection.
        """
        if kwargs.get("kill", False):
            super(SqlExecCommand, self).run(**kwargs)

        self.reset_log()
        # self.log("----------")
        # for (k, v) in kwargs.iteritems():
        #     self.log(str(k) + " : " + str(v))
        # self.log("----------")

        view = self.window.active_view()
        action = kwargs.pop("action", "")
        if action == "reset":
            view.settings().erase("cmd")

        # If scope was passed in then save it on view.
        # If scope is saved on view then use it (only if not passed in).
        # Must pop scope off kwargs before attempting to run anything.
        # If you don't then things won't run.
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

        # save cmd and dialect against view so subseqeuent runs can reuse it
        # (press F7 or ctrl+b to reset)
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
