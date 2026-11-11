"""The tool catalogue an agent may call.

One registry, three sources, one contract: a tool has a name, a description,
an argument hint, a risk level and a ``run`` callable returning text plus
structured data. Keeping them uniform is what lets the loop stay small and
lets the model see a single flat list.

Risk vocabulary:
  * ``safe``        read-only, no side effects outside Tolesh
  * ``elevated``    runs code or reads the network; sandboxed, auto-allowed
  * ``consequential`` changes something outside Tolesh; needs approval
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_OBSERVATION = 6000


@dataclass
class ToolResult:
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "ok": self.ok, "truncated": self.truncated,
                "data": self.data}


@dataclass
class ToolSpec:
    name: str
    description: str
    args_hint: str
    risk: str
    source: str
    # ``approved`` is True only when a human approved this exact call. Tools
    # that change external state must check it rather than assume it.
    run: Callable[[dict[str, Any], bool], ToolResult]
    needs_approval: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "args": self.args_hint, "risk": self.risk,
                "source": self.source, "needs_approval": self.needs_approval}


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec] | None = None):
        self._tools: dict[str, ToolSpec] = {t.name: t for t in (tools or [])}

    def add(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools)

    def catalogue_text(self, *, limit: int = 40) -> str:
        """Compact description for the prompt.

        Capped on purpose: a 200-tool catalogue would consume the context the
        agent needs for the actual task.
        """
        lines = []
        for tool in self.list()[:limit]:
            lines.append(f"- {tool.name}({tool.args_hint}): {tool.description}"
                         + (" [needs approval]" if tool.needs_approval else ""))
        if len(self._tools) > limit:
            lines.append(f"- … {len(self._tools) - limit} more tools are available "
                         "but were omitted to save context.")
        return "\n".join(lines)

    def invoke(self, name: str, args: dict[str, Any], *,
               approved: bool = False) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(text=f"Unknown tool: {name}. Available tools: "
                                   + ", ".join(sorted(self._tools)[:20]), ok=False)
        try:
            result = tool.run(args or {}, approved)
        except Exception as exc:
            return ToolResult(text=f"The tool {name} failed: {exc}", ok=False)
        if len(result.text) > MAX_OBSERVATION:
            result = ToolResult(text=result.text[:MAX_OBSERVATION] + "\n[truncated]",
                                data=result.data, ok=result.ok, truncated=True)
        return result


def build_registry(ctx: Any, *, include_connectors: bool = True) -> ToolRegistry:
    """Assemble the tools available in this workspace.

    Missing subsystems are skipped rather than replaced with stubs: a tool that
    always answers "not available" teaches the model to distrust the catalogue.
    """
    registry = ToolRegistry()

    # ---------------------------------------------------------- sandboxed code
    if getattr(ctx, "codeexec", None) is not None:
        def run_python(args: dict[str, Any], approved: bool = False) -> ToolResult:
            result = ctx.codeexec.run_python(
                str(args.get("code") or ""),
                timeout=int(args.get("timeout") or 20),
                memory_mb=int(args.get("memory_mb") or 512),
                isolate_network=True)
            text = (result.get("stdout") or "")
            if result.get("stderr"):
                text += "\n[stderr]\n" + result["stderr"]
            if result.get("message"):
                text = f"{text}\n[{result['message']}]".strip()
            return ToolResult(text=text or "(no output)", data=result,
                              ok=bool(result.get("ok")))

        registry.add(ToolSpec(
            name="run_python",
            description="Run Python in an isolated sandbox (no network, no API keys, "
                        "temporary working directory). Use it for calculations, data "
                        "analysis and creating files.",
            args_hint="code: str, timeout: int = 20",
            risk="elevated", source="sandbox", run=run_python))

    # ------------------------------------------------------------- web access
    if getattr(ctx, "netsearch", None) is not None:
        def web_fetch(args: dict[str, Any], approved: bool = False) -> ToolResult:
            page = ctx.netsearch.fetch_page(str(args.get("url") or ""))
            return ToolResult(text=page.get("text", ""), data=page)

        def web_search(args: dict[str, Any], approved: bool = False) -> ToolResult:
            payload = ctx.netsearch.search(str(args.get("query") or ""), limit=5)
            lines = [f"{r['title']} — {r['url']}\n{r.get('snippet', '')}"
                     for r in payload["results"]]
            return ToolResult(text="\n\n".join(lines) or "(no results)", data=payload)

        registry.add(ToolSpec(
            name="web_fetch",
            description="Read one web page as text. Private and loopback addresses "
                        "are refused.",
            args_hint="url: str", risk="safe", source="web", run=web_fetch))
        registry.add(ToolSpec(
            name="web_search",
            description="Search the web and return titles, URLs and snippets.",
            args_hint="query: str", risk="safe", source="web", run=web_search))

    # --------------------------------------------------------- local documents
    if getattr(ctx, "attachments", None) is not None:
        def search_files(args: dict[str, Any], approved: bool = False) -> ToolResult:
            hits = ctx.attachments.retrieve(str(args.get("query") or ""),
                                            limit=int(args.get("limit") or 5))
            if not hits:
                return ToolResult(text="No matching passage in the uploaded files.")
            text = "\n\n".join(f"[{h['filename']}]\n{h['text']}" for h in hits)
            return ToolResult(text=text, data={"hits": len(hits)})

        registry.add(ToolSpec(
            name="search_files",
            description="Search the user's uploaded files and return matching passages.",
            args_hint="query: str", risk="safe", source="files", run=search_files))

    # -------------------------------------------------------------- artifacts
    if getattr(ctx, "artifacts", None) is not None:
        def _read_attachment(attachment_id: str) -> tuple[bytes, str]:
            if not attachment_id:
                raise ValueError("attachment_id is required: upload the file first.")
            meta = ctx.attachments.get(attachment_id) if ctx.attachments else None
            if meta is None:
                raise ValueError(f"No uploaded file with id {attachment_id}.")
            data = ctx.attachments.read_bytes(attachment_id)
            if data is None:
                raise ValueError("That upload's file is no longer on disk.")
            return data, meta["filename"]

        def analyse_dataset(args: dict[str, Any], approved: bool = False) -> ToolResult:
            from ..artifacts import summarise, summarise_text

            data, name = _read_attachment(str(args.get("attachment_id") or ""))
            profile = summarise(data, name=name)
            return ToolResult(text=summarise_text(profile), data=profile)

        def make_chart(args: dict[str, Any], approved: bool = False) -> ToolResult:
            from ..artifacts import chart_png

            data, name = _read_attachment(str(args.get("attachment_id") or ""))
            png, meta = chart_png(
                data, chart_type=str(args.get("chart_type") or "bar"),
                x=str(args.get("x") or ""), y=str(args.get("y") or ""),
                title=str(args.get("title") or ""), name=name)
            artifact = ctx.artifacts.save_bytes(
                f"{meta['chart_type']}-{meta['x']}.png", png, kind="image",
                mime="image/png", meta=meta,
                conversation_id=args.get("conversation_id") or None)
            return ToolResult(
                text=f"Chart saved as artifact {artifact['id']} "
                     f"({artifact['size']} bytes).",
                data={"artifact": artifact})

        def make_report(args: dict[str, Any], approved: bool = False) -> ToolResult:
            from ..artifacts import report_html

            sections = args.get("sections")
            if isinstance(sections, str):
                sections = [{"heading": "", "body": sections}]
            if not isinstance(sections, list):
                sections = []
            page = report_html(str(args.get("title") or "Report"), sections)
            artifact = ctx.artifacts.save_text(
                f"{args.get('title') or 'report'}.html", page, kind="report",
                mime="text/html",
                conversation_id=args.get("conversation_id") or None)
            return ToolResult(text=f"Report saved as artifact {artifact['id']}.",
                              data={"artifact": artifact})

        def save_text_artifact(args: dict[str, Any], approved: bool = False) -> ToolResult:
            name = str(args.get("name") or "note.md")
            artifact = ctx.artifacts.save_text(
                name, str(args.get("content") or ""),
                conversation_id=args.get("conversation_id") or None)
            return ToolResult(text=f"Saved {artifact['name']} as {artifact['id']}.",
                              data={"artifact": artifact})

        def list_artifacts(args: dict[str, Any], approved: bool = False) -> ToolResult:
            items = ctx.artifacts.list(limit=30)
            if not items:
                return ToolResult(text="No artifacts yet.")
            return ToolResult(text="\n".join(
                f"- {a['id']} {a['name']} ({a['kind']}, {a['size']} bytes)"
                for a in items), data={"artifacts": items})

        registry.add(ToolSpec(
            name="analyse_dataset",
            description="Profile an uploaded CSV/Excel/JSON table: columns, types, "
                        "missing values, ranges and a sample.",
            args_hint="attachment_id: str", risk="safe", source="artifacts",
            run=analyse_dataset))
        registry.add(ToolSpec(
            name="make_chart",
            description="Draw a bar, line, scatter, hist or pie chart from an uploaded "
                        "table and save it as a downloadable image.",
            args_hint="attachment_id: str, chart_type: str, x: str, y?: str, title?: str",
            risk="safe", source="artifacts", run=make_chart))
        registry.add(ToolSpec(
            name="make_report",
            description="Render an HTML report with headings and body text, saved as a "
                        "downloadable artifact.",
            args_hint='title: str, sections: [{"heading": str, "body": str}]',
            risk="safe", source="artifacts", run=make_report))
        registry.add(ToolSpec(
            name="save_text_artifact",
            description="Save text (Markdown, code, notes) as a downloadable file.",
            args_hint="name: str, content: str", risk="safe", source="artifacts",
            run=save_text_artifact))
        registry.add(ToolSpec(
            name="list_artifacts",
            description="List the files produced so far.",
            args_hint="", risk="safe", source="artifacts", run=list_artifacts))

    # ------------------------------------------------------------------ media
    if getattr(ctx, "media", None) is not None:
        def describe_image(args: dict[str, Any], approved: bool = False) -> ToolResult:
            attachment_id = str(args.get("attachment_id") or "")
            meta = ctx.attachments.get(attachment_id) if ctx.attachments else None
            if meta is None:
                return ToolResult(text=f"No uploaded image with id {attachment_id}.", ok=False)
            data = ctx.attachments.read_bytes(attachment_id)
            if data is None:
                return ToolResult(text="That upload is no longer on disk.", ok=False)
            try:
                result = ctx.media.describe_image(
                    ctx.router, data, prompt=str(args.get("prompt") or "Describe this image."),
                    model=args.get("model") or None, mime=meta["mime"] or "image/png")
            except Exception as exc:
                return ToolResult(text=str(exc), ok=False)
            return ToolResult(text=result["text"], data=result)

        def generate_image(args: dict[str, Any], approved: bool = False) -> ToolResult:
            try:
                image, meta = ctx.media.generate_image(
                    str(args.get("prompt") or ""),
                    size=str(args.get("size") or "1024x1024"))
            except Exception as exc:
                return ToolResult(text=str(exc), ok=False)
            if ctx.artifacts is None:
                return ToolResult(text="Generated an image but no artifact store is available.",
                                  ok=False)
            artifact = ctx.artifacts.save_bytes(
                f"image-{meta['model']}.png", image, kind="image", mime="image/png",
                meta=meta)
            return ToolResult(text=f"Image saved as artifact {artifact['id']}.",
                              data={"artifact": artifact})

        registry.add(ToolSpec(
            name="describe_image",
            description="Look at an uploaded image and answer a question about it.",
            args_hint="attachment_id: str, prompt?: str", risk="safe",
            source="media", run=describe_image))
        registry.add(ToolSpec(
            name="generate_image",
            description="Generate an image from a text description and save it as a "
                        "downloadable artifact.",
            args_hint="prompt: str, size?: str", risk="elevated", source="media",
            run=generate_image))

    # -------------------------------------------------------- system controls
    # Three narrow controls, each needing an approval. The tool layer never
    # invents a fourth: there is no "run this command" tool, and there will not
    # be one here.
    computer = getattr(ctx, "tools", None)
    if computer is not None:
        def system_tool(name: str) -> Callable[[dict[str, Any], bool], ToolResult]:
            def call(args: dict[str, Any], approved: bool = False) -> ToolResult:
                if not approved:
                    # The loop asks the gate before this is reached; reaching it
                    # unapproved means something bypassed the gate.
                    return ToolResult(
                        text=f"{name} needs the user's approval before it runs.",
                        ok=False, data={"needs_approval": True})
                result = computer.execute(name, args)
                ok = result.get("status") == "ok"
                if ok:
                    text = ("The screen is locked." if name == "lock_screen"
                            else f"{name.replace('_', ' ')} is now "
                                 f"{result.get('level')}%.")
                else:
                    text = str(result.get("message") or f"{name} failed.")
                return ToolResult(text=text, data=result, ok=ok)
            return call

        registry.add(ToolSpec(
            name="set_volume",
            description="Set the system output volume to a percentage (0–100).",
            args_hint="level: int", risk="elevated", source="system",
            run=system_tool("set_volume"), needs_approval=True))
        registry.add(ToolSpec(
            name="set_brightness",
            description="Set the display brightness to a percentage (0–100).",
            args_hint="level: int", risk="elevated", source="system",
            run=system_tool("set_brightness"), needs_approval=True))
        registry.add(ToolSpec(
            name="lock_screen",
            description="Lock the screen.",
            args_hint="", risk="elevated", source="system",
            run=system_tool("lock_screen"), needs_approval=True))

    # ------------------------------------------------------------------- MCP
    mcp = getattr(ctx, "mcp", None)
    if mcp is not None:
        try:
            for tool in mcp.all_tools():
                def make(tool_info: dict[str, Any]) -> Callable[[dict[str, Any], bool], ToolResult]:
                    def call(args: dict[str, Any], approved: bool = False) -> ToolResult:
                        result = mcp.invoke(
                            tool_info["server"], tool_info["name"], args,
                            # The loop sets ``approved`` only after the gate
                            # confirmed this exact call.
                            approval={"status": "approved"} if approved else None)
                        if result.get("status") == "pending-approval":
                            return ToolResult(
                                text="This tool needs the user's approval before it runs.",
                                ok=False, data={"approval": result.get("approval")})
                        return ToolResult(text=result.get("text") or result.get("message", ""),
                                          data=result,
                                          ok=result.get("status") == "ok")
                    return call

                registry.add(ToolSpec(
                    name=tool["qualified"],
                    description=tool.get("description") or f"{tool['name']} on {tool['server']}",
                    args_hint=_schema_hint(tool.get("input_schema") or {}),
                    risk="elevated" if tool.get("permission") == "allow" else "consequential",
                    source=f"mcp:{tool['server']}",
                    run=make(tool),
                    needs_approval=tool.get("permission") == "ask"))
        except Exception:
            # A broken MCP server must not remove every other tool.
            pass

    # ------------------------------------------------------------ connectors
    if include_connectors and getattr(ctx, "connectors", None) is not None:
        try:
            for connection in ctx.connectors.connections():
                if connection["status"] not in ("connected", "expired"):
                    continue
                for action in ctx.connectors.actions(connection["id"]):
                    def make_action(connection_id: str, action_name: str,
                                    write: bool) -> Callable[[dict[str, Any], bool], ToolResult]:
                        def call(args: dict[str, Any], approved: bool = False) -> ToolResult:
                            try:
                                result = ctx.connectors.run_action(
                                    connection_id, action_name, args, approved=approved)
                            except Exception as exc:
                                if getattr(exc, "kind", "") == "approval":
                                    return ToolResult(
                                        text="This action changes data outside Tolesh "
                                             "and needs the user's approval.", ok=False,
                                        data={"requires_approval": True,
                                              "connection_id": connection_id,
                                              "action": action_name, "args": args})
                                return ToolResult(text=str(exc), ok=False)
                            return ToolResult(text=json.dumps(result, ensure_ascii=False),
                                              data=result)
                        return call

                    registry.add(ToolSpec(
                        name=f"connector__{connection['id']}__{action['name']}",
                        description=f"{connection['label']}: {action['name']}",
                        args_hint="arguments depend on the action; ask for the ids you need",
                        risk="consequential" if action["write"] else "safe",
                        source=f"connector:{connection['provider']}",
                        run=make_action(connection["id"], action["name"], action["write"]),
                        needs_approval=action["write"]))
        except Exception:
            pass

    return registry


def _schema_hint(schema: dict[str, Any]) -> str:
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict) or not props:
        return ""
    required = set(schema.get("required") or [])
    parts = []
    for key, spec in list(props.items())[:8]:
        kind = ""
        if isinstance(spec, dict):
            kind = str(spec.get("type") or "")
        parts.append(f"{key}{': ' + kind if kind else ''}"
                     + ("" if key in required else "?"))
    return ", ".join(parts)
