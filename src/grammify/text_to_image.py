import pictex
from collections.abc import Mapping, Callable
import re
import textwrap


def wrap_text(text: str, width: int) -> list[str]:
    # 1. Extract all tags to a list
    tags = re.findall(r"<[^>]+>", text)

    # 2. Replace tags with a placeholder char (e.g., \0)
    #    We use \0 because textwrap treats it as a non-breaking character.
    placeholder_text = re.sub(r"<[^>]+>", "\0", text)

    # 3. Wrap using the standard library
    wrapped_lines = textwrap.wrap(placeholder_text, width=width)

    # 4. Re-inject the original tags back into position
    tag_iter = iter(tags)
    final_lines = []
    for line in wrapped_lines:
        # Replace each \0 with the next tag from our list
        reconstructed = re.sub(r"\0", lambda m: next(tag_iter), line)
        final_lines.append(reconstructed)

    return final_lines


def tagged_text_to_image(
    tagged_text: str,
    max_letters_in_a_row: int,
    each_tag_style: Mapping[str, Callable[[pictex.Text], pictex.Text]],
    output_path: str,
) -> None:
    wrapped_text = wrap_text(tagged_text, width=max_letters_in_a_row)

    rows = []
    # Tracks which styles are currently "ON"
    each_tag_state = {tag: False for tag in each_tag_style.keys()}

    # Regex to find tags like <b> or </b>
    tag_pattern = re.compile(r"<(/?\w+)>")

    for line in wrapped_text:
        current_row_texts = []
        last_pos = 0

        # Find all tag occurrences in the current line
        for match in tag_pattern.finditer(line):
            # 1. Handle the text BEFORE the tag
            plain_text = line[last_pos : match.start()]
            if plain_text:
                current_text = pictex.Text(text=plain_text)
                # Apply styles for all currently active tags
                for tag, is_active in each_tag_state.items():
                    if is_active:
                        current_text = each_tag_style[tag](current_text)
                current_row_texts.append(current_text)

            # 2. Update the state based on the tag found
            tag_content = match.group(1)  # e.g., "b" or "/b"
            is_closing = tag_content.startswith("/")
            tag_name = tag_content.strip("/")

            if tag_name in each_tag_state:
                each_tag_state[tag_name] = not is_closing

            # Move the pointer to the end of the current tag
            last_pos = match.end()

        # 3. Handle any remaining text after the last tag in the line
        remaining_text = line[last_pos:]
        if remaining_text:
            current_text = pictex.Text(text=remaining_text)
            for tag, is_active in each_tag_state.items():
                if is_active:
                    current_text = each_tag_style[tag](current_text)
            current_row_texts.append(current_text)

        rows.append(pictex.Row(*current_row_texts).padding(5))

    column = pictex.Column(*rows)
    return (
        pictex.Canvas()
        .padding(30)
        .background_color("white")
        .render(column)
        .save(output_path)
    )
