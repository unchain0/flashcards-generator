use super::*;

#[test]
fn restores_all_notations_and_resets_between_cards() {
    let text = r"á $$x^2$$ e \[y\], \(z\), $a$";
    let mut processor = MathProcessor::default();
    let protected = processor.extract_and_replace(text);
    assert_eq!(
        protected,
        "á MATHPLACEHOLDER0001 e MATHPLACEHOLDER0002, MATHPLACEHOLDER0003, MATHPLACEHOLDER0004"
    );
    assert_eq!(processor.restore_math(&protected), text);
    assert_eq!(processor.extract_and_replace("plain"), "plain");
    assert_eq!(processor.restore_math(&protected), protected);
}

#[test]
fn segments_preserve_unicode_and_empty_input() {
    assert_eq!(extract_math_segments(""), vec![("", false)]);
    assert_eq!(extract_math_segments("água"), vec![("água", false)]);
    assert_eq!(
        extract_math_segments(r"á $x$\[y\] fim"),
        vec![
            ("á ", false),
            ("$x$", true),
            (r"\[y\]", true),
            (" fim", false)
        ]
    );
    assert_eq!(extract_math_segments("$$x$$"), vec![("$$x$$", true)]);
    assert!(has_math(r"\(x\)"));
    assert!(!has_math("$unfinished"));
}

#[test]
fn converts_display_before_inline_without_reinterpreting_latex() {
    assert_eq!(
        convert_to_anki_math_format(r"$$x$$ $y$ \(z\)"),
        r"\[x\] \(y\) \(z\)"
    );
    assert_eq!(
        create_cloze_with_math("$x$ e $$y$$", 2),
        r"{{c2::\(x\) e \[y\]}}"
    );
    assert_eq!(create_cloze_with_math("água", 1), "{{c1::água}}");
    assert_eq!(convert_to_anki_math_format("$"), "$");
}
