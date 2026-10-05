use super::*;

#[test]
fn preserves_fixed_overlap_and_whole_chapter_filtering() {
    let planner = PdfChunkPlanner::default();
    for (total, expected) in [
        (0, vec![]),
        (1, std::iter::once(0..1).collect()),
        (30, std::iter::once(0..30).collect()),
        (31, vec![0..30, 25..31]),
        (55, vec![0..30, 25..55]),
        (56, vec![0..30, 25..55, 50..56]),
    ] {
        let actual = planner.plan(total, &[], false).unwrap();
        assert_eq!(
            actual
                .into_iter()
                .flat_map(|chunk| chunk.pages)
                .collect::<Vec<_>>(),
            expected
        );
    }
    let chapters = [
        Chapter {
            pages: 2..10,
            title: "Preface".into(),
        },
        Chapter {
            pages: 10..45,
            title: "Cells".into(),
        },
        Chapter {
            pages: 45..60,
            title: "Genetics".into(),
        },
        Chapter {
            pages: 60..70,
            title: "Bibliography".into(),
        },
    ];
    let chunks = planner.plan(70, &chapters, false).unwrap();
    assert_eq!(chunks[0].pages, vec![10..45]);
    assert_eq!(chunks[1].pages, vec![45..60, 60..70]);
    assert_eq!(chunks[1].titles, vec!["Genetics", "Bibliography"]);
    let overlapping = planner.plan(70, &chapters, true).unwrap();
    assert_eq!(overlapping[1].pages, vec![40..45, 45..60, 60..70]);
    let prefix = planner
        .plan(
            12,
            &[Chapter {
                pages: 2..12,
                title: "Cells".into(),
            }],
            false,
        )
        .unwrap();
    assert_eq!(prefix[0].pages, vec![0..2, 2..12]);
    assert_eq!(
        planner.plan(70, &chapters[..1], false).unwrap(),
        Vec::<PdfChunk>::new()
    );
    assert!(planner.plan(10_001, &[], false).is_err());
    assert!(PdfChunkPlanner::new(0, 0).is_err());
    assert!(PdfChunkPlanner::new(30, 30).is_err());
    assert!(PdfChunkPlanner::new(usize::MAX, 0).is_err());
}

#[test]
fn retains_page_order_and_duplicate_pages_in_irregular_outlines() {
    let planner = PdfChunkPlanner::new(10, 0).unwrap();
    let chapters = [
        Chapter {
            pages: 2..5,
            title: "Células".into(),
        },
        Chapter {
            pages: 3..7,
            title: "Células".into(),
        },
        Chapter {
            pages: 9..usize::MAX,
            title: "DNA".into(),
        },
    ];
    let chunks = planner.plan(12, &chapters, false).unwrap();
    assert_eq!(chunks[0].pages, vec![0..2, 2..5, 3..7]);
    assert_eq!(chunks[0].titles, vec!["Células"]);
    assert_eq!(chunks[1].pages, vec![9..12]);
    let invalid_bounds = [Chapter {
        pages: std::ops::Range {
            start: usize::MAX,
            end: 0,
        },
        title: "DNA".into(),
    }];
    let clamped = planner.plan(12, &invalid_bounds, false).unwrap();
    assert_eq!(clamped[0].pages, Vec::<Range<usize>>::new());
    let clamped = PdfChunkPlanner::default()
        .plan(12, &invalid_bounds, false)
        .unwrap();
    assert_eq!(clamped[0].pages, vec![0..12]);
    let filtered = planner
        .plan(
            12,
            &[Chapter {
                pages: 0..12,
                title: "INDEX".into(),
            }],
            false,
        )
        .unwrap();
    assert_eq!(filtered, Vec::<PdfChunk>::new());
    assert!(
        planner
            .plan(
                1,
                &vec![
                    Chapter {
                        pages: 0..1,
                        title: "DNA".into()
                    };
                    10_001
                ],
                false
            )
            .is_err()
    );
}

#[test]
fn custom_overlap_never_creates_empty_ranges_and_rejects_unbounded_page_counts() {
    let chapters = [
        Chapter {
            pages: 0..6,
            title: "Cells".into(),
        },
        Chapter {
            pages: 6..12,
            title: "Genetics".into(),
        },
    ];
    for (overlap, expected) in [
        (0, std::iter::once(6..12).collect::<Vec<_>>()),
        (3, vec![3..6, 6..12]),
    ] {
        let planner = PdfChunkPlanner::new(6, overlap).unwrap();
        let chunks = planner.plan(12, &chapters, true).unwrap();
        assert_eq!(chunks.len(), 2);
        assert_eq!(chunks[0].pages, std::iter::once(0..6).collect::<Vec<_>>());
        assert_eq!(chunks[1].pages, expected);
        assert!(
            chunks
                .iter()
                .flat_map(|chunk| &chunk.pages)
                .all(|range| !range.is_empty())
        );
        assert!(planner.plan(usize::MAX, &[], false).is_err());
    }
    let planner = PdfChunkPlanner::new(10_000, 0).unwrap();
    let chunks = planner.plan(10_000, &[], false).unwrap();
    assert_eq!(chunks.len(), 1);
    assert_eq!(
        chunks[0].pages,
        std::iter::once(0..10_000).collect::<Vec<_>>()
    );
    let error = planner.plan(10_001, &[], false).unwrap_err();
    assert_eq!(error, InvalidChunkPlan);
    assert_eq!(
        error.to_string(),
        "Invalid PDF page count or chunk configuration"
    );
    assert!(error.source().is_none());
}
