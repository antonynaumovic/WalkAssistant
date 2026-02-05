
import flet as ft
from flet import Checkbox, FloatingActionButton, Icons, Page, TextField


async def main(page: ft.Page):
    async def store_value(key, value, alert):
        await ft.SharedPreferences().set(key, value)
        if alert:
            page.show_dialog(ft.SnackBar(f"{key} saved to SharedPreferences"))
        return True

    async def get_value(key):
        contents = await ft.SharedPreferences().get(key)
        page.add(ft.Text(f"SharedPreferences contents: {contents}"))
        return contents

    page.title = "Walk Assistant"
    page.add(ft.Text(value="Hello, world!"))

    page.adaptive = True

    page.appbar = ft.AppBar(

        leading=ft.TextButton("New", style=ft.ButtonStyle(padding=0)),

        title=ft.Text("Adaptive AppBar"),

        actions=[

            ft.IconButton(ft.cupertino_icons.ADD, style=ft.ButtonStyle(padding=0))

        ],

        bgcolor=ft.Colors.with_opacity(0.04, ft.CupertinoColors.SYSTEM_BACKGROUND),

    )


if __name__ == "__main__":
    ft.run(main)
