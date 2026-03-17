"""Comprehensive path handling tests for various scenarios."""

from flashcards_generator.application.use_cases import GenerateFlashcardsUseCase


class TestPathScenarios:
    """Test various file path organizations."""

    def test_organized_two_level_structure(self, tmp_path, mock_generator):
        """Test well-organized two-level structure: input/course/lesson/file.pdf"""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Create organized structure
        course_dir = input_dir / "BANRISUL" / "Técnico em TI"
        course_dir.mkdir(parents=True)
        pdf_file = course_dir / "Aula 01.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())
        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        # Should create: output/BANRISUL/Técnico em TI/
        expected = output_dir / "BANRISUL" / "Técnico em TI"
        assert result == expected
        assert result.exists()

    def test_deep_nesting_truncated_to_two_levels(self, tmp_path, mock_generator):
        """Test deep nesting gets truncated: input/a/b/c/d/e/file.pdf -> output/a/b/"""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Create deep structure
        deep_dir = input_dir / "pasta1" / "pasta2" / "pasta3" / "pasta4" / "pasta5"
        deep_dir.mkdir(parents=True)
        pdf_file = deep_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())
        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        # Should only use first 2 levels: output/pasta1/pasta2/
        expected = output_dir / "pasta1" / "pasta2"
        assert result == expected

    def test_single_level_structure(self, tmp_path, mock_generator):
        """Test single level structure: input/course/file.pdf -> output/course/"""
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

        # Should create: output/Course/
        expected = output_dir / "Course"
        assert result == expected

    def test_flat_structure_no_subdirs(self, tmp_path, mock_generator):
        """Test flat structure: input/file.pdf -> output/"""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        pdf_file = input_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())
        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        # Should return output directly
        assert result == output_dir

    def test_special_characters_in_path(self, tmp_path, mock_generator):
        """Test paths with special characters: input/curso@madruga/file.pdf"""
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

        expected = output_dir / "curso@madruga" / "aula#01"
        assert result == expected

    def test_spaces_and_unicode_in_path(self, tmp_path, mock_generator):
        """Test paths with spaces and unicode: input/BANRISUL Técnico/aula™/file.pdf"""
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

        expected = output_dir / "BANRISUL Técnico" / "aula™"
        assert result == expected

    def test_very_long_directory_names(self, tmp_path, mock_generator):
        """Test paths with very long names (truncated at 2 levels)."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Create long directory name
        long_name = "A" * 100
        long_dir = input_dir / long_name / "subfolder" / "another" / "deep"
        long_dir.mkdir(parents=True)
        pdf_file = long_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())
        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        # Should only use first 2 levels
        expected = output_dir / long_name / "subfolder"
        assert result == expected

    def test_mixed_case_paths(self, tmp_path, mock_generator):
        """Test paths with mixed case: input/Course/Module/Lesson/file.pdf"""
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

        expected = output_dir / "Course" / "Module"
        assert result == expected

    def test_numeric_folder_names(self, tmp_path, mock_generator):
        """Test paths with numeric names: input/2024/01/file.pdf"""
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

        expected = output_dir / "2024" / "01"
        assert result == expected

    def test_dots_in_folder_names(self, tmp_path, mock_generator):
        """Test paths with dots: input/v1.0/module.1/lesson.pdf"""
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

        expected = output_dir / "v1.0" / "module.1"
        assert result == expected

    def test_hyphens_and_underscores(self, tmp_path, mock_generator):
        """Test paths with hyphens and underscores."""
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

        expected = output_dir / "my-course" / "my_module"
        assert result == expected

    def test_multiple_pdfs_same_folder(self, tmp_path, mock_generator):
        """Test multiple PDFs in same folder get same output subdir."""
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

        # Both should go to same folder
        expected = output_dir / "Course" / "Module"
        assert result1 == expected
        assert result2 == expected
        assert result1 == result2
