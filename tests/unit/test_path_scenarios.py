from flashcards_generator.application.use_cases import GenerateFlashcardsUseCase


class TestPathScenarios:
    def test_nested_source_preserves_full_relative_path(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        pdf_dir = input_dir / "BANRISUL" / "Técnico em TI"
        pdf_dir.mkdir(parents=True)
        pdf_file = pdf_dir / "Aula 01.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        expected = output_dir / "BANRISUL" / "Técnico em TI"
        assert result == expected
        assert result.exists()

    def test_deep_nesting_keeps_all_parent_levels(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        pdf_dir = input_dir / "pasta1" / "pasta2" / "pasta3" / "pasta4" / "pasta5"
        pdf_dir.mkdir(parents=True)
        pdf_file = pdf_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        assert (
            result == output_dir / "pasta1" / "pasta2" / "pasta3" / "pasta4" / "pasta5"
        )

    def test_single_level_structure_uses_matching_output_subdir(
        self, tmp_path, mock_generator
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        course_dir = input_dir / "Course"
        course_dir.mkdir()
        pdf_file = course_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        assert result == output_dir / "Course"

    def test_flat_structure_uses_output_root(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        pdf_file = input_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        assert result == output_dir

    def test_special_characters_in_path(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        special_dir = input_dir / "curso@madruga" / "aula#01"
        special_dir.mkdir(parents=True)
        pdf_file = special_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        assert result == output_dir / "curso@madruga" / "aula#01"

    def test_spaces_and_unicode_in_path(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        unicode_dir = input_dir / "BANRISUL Técnico" / "aula™"
        unicode_dir.mkdir(parents=True)
        pdf_file = unicode_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        assert result == output_dir / "BANRISUL Técnico" / "aula™"

    def test_very_long_directory_names(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        long_name = "A" * 100
        pdf_dir = input_dir / long_name / "subfolder" / "another" / "deep"
        pdf_dir.mkdir(parents=True)
        pdf_file = pdf_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        assert result == output_dir / long_name / "subfolder" / "another" / "deep"

    def test_mixed_case_paths(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        mixed_dir = input_dir / "Course" / "Module" / "Lesson"
        mixed_dir.mkdir(parents=True)
        pdf_file = mixed_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        assert result == output_dir / "Course" / "Module" / "Lesson"

    def test_numeric_folder_names(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        numeric_dir = input_dir / "2024" / "01" / "15"
        numeric_dir.mkdir(parents=True)
        pdf_file = numeric_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        assert result == output_dir / "2024" / "01" / "15"

    def test_dots_in_folder_names(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        dots_dir = input_dir / "v1.0" / "module.1" / "sub.2"
        dots_dir.mkdir(parents=True)
        pdf_file = dots_dir / "lesson.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        assert result == output_dir / "v1.0" / "module.1" / "sub.2"

    def test_hyphens_and_underscores(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        hyphen_dir = input_dir / "my-course" / "my_module" / "my-lesson"
        hyphen_dir.mkdir(parents=True)
        pdf_file = hyphen_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        assert result == output_dir / "my-course" / "my_module" / "my-lesson"

    def test_multiple_pdfs_same_folder_share_output_subdir(
        self, tmp_path, mock_generator
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        course_dir = input_dir / "Course" / "Module"
        course_dir.mkdir(parents=True)
        pdf1 = course_dir / "lesson1.pdf"
        pdf1.write_text("PDF 1")
        pdf2 = course_dir / "lesson2.pdf"
        pdf2.write_text("PDF 2")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        result1 = use_case._get_output_subdir(pdf1, input_dir, output_dir)
        result2 = use_case._get_output_subdir(pdf2, input_dir, output_dir)

        expected = output_dir / "Course" / "Module"
        assert result1 == expected
        assert result2 == expected
        assert result1 == result2
