use super::*;

fn fixture() -> (tempfile::TempDir, Metadata) {
    let root = tempfile::tempdir().unwrap();
    let packages = PACKAGES
        .iter()
        .map(|(name, directory, _, _)| {
            let directory = root.path().join("rust").join(directory);
            fs::create_dir_all(&directory).unwrap();
            let manifest_path = directory.join("Cargo.toml");
            fs::write(&manifest_path, "[lints]\nworkspace = true\n").unwrap();
            Package {
                id: format!("path+{}#{name}@1.0.0", directory.display()),
                name: (*name).to_owned(),
                source: None,
                manifest_path,
                dependencies: Vec::new(),
            }
        })
        .collect::<Vec<_>>();
    let metadata = Metadata {
        workspace_members: packages.iter().map(|package| package.id.clone()).collect(),
        workspace_root: root.path().to_owned(),
        resolve: Resolve {
            nodes: packages
                .iter()
                .map(|package| Node {
                    id: package.id.clone(),
                    deps: Vec::new(),
                })
                .collect(),
        },
        packages,
        version: 1,
    };
    (root, metadata)
}

#[test]
fn rejects_outward_local_dependencies_for_every_dependency_kind() {
    for kind in [None, Some("dev"), Some("build")] {
        let (_root, mut metadata) = fixture();
        let target = metadata.packages[2]
            .manifest_path
            .parent()
            .unwrap()
            .to_owned();
        metadata.packages[0].dependencies.push(Dependency {
            name: "renamed-service".into(),
            path: Some(target),
            kind: kind.map(str::to_owned),
        });
        assert!(validate(&metadata).is_err());
    }
}

#[test]
fn validates_resolved_ids_independently_of_dependency_names() {
    let (_root, mut metadata) = fixture();
    metadata.resolve.nodes[4].deps.push(ResolvedDependency {
        pkg: metadata.packages[0].id.clone(),
    });
    assert!(validate(&metadata).is_ok());
    metadata.resolve.nodes[0].deps.push(ResolvedDependency {
        pkg: metadata.packages[4].id.clone(),
    });
    assert!(validate(&metadata).is_err());
}

#[test]
fn rejects_unclassified_packages_missing_lints_and_unexpected_external_capabilities() {
    let (_root, mut metadata) = fixture();
    assert!(validate(&metadata).is_ok());
    metadata.packages[0].dependencies.push(Dependency {
        name: "axum".into(),
        path: None,
        kind: None,
    });
    assert!(validate(&metadata).is_err());
    metadata.packages[0].dependencies.clear();
    fs::write(
        &metadata.packages[0].manifest_path,
        "[lints]\nworkspace = false\n",
    )
    .unwrap();
    assert!(validate(&metadata).is_err());
    fs::write(
        &metadata.packages[0].manifest_path,
        "[lints]\nworkspace = true\n",
    )
    .unwrap();
    metadata.packages[0].name = "unknown-domain".into();
    assert!(validate(&metadata).is_err());
    metadata.version = 2;
    assert!(validate(&metadata).is_err());
}

#[test]
fn enforces_external_allowlists_and_bounded_input() {
    for (layer, kind, name, allowed) in [
        (0, None, "serde", false),
        (1, None, "regex", true),
        (1, None, "sqlx", false),
        (1, Some("dev"), "serde_json", true),
        (2, None, "serde", true),
        (2, None, "reqwest", false),
        (2, Some("build"), "serde_json", false),
        (3, None, "reqwest", true),
    ] {
        let dependency = Dependency {
            name: name.into(),
            path: None,
            kind: kind.map(str::to_owned),
        };
        assert_eq!(external_allowed(layer, &dependency), allowed);
    }
    let directory = tempfile::tempdir().unwrap();
    let file = directory.path().join("metadata.json");
    fs::write(&file, "12345").unwrap();
    assert!(read_bounded(&file, 4).is_err());
    assert_eq!(read_bounded(&file, 5).unwrap(), b"12345");
}

#[test]
fn rejects_local_capabilities_in_server_and_shared_declarations_for_every_kind() {
    for (source, target) in [(9, 3), (7, 3), (6, 3), (8, 4), (6, 7)] {
        for kind in [None, Some("dev"), Some("build")] {
            let (_root, mut metadata) = fixture();
            let path = metadata.packages[target]
                .manifest_path
                .parent()
                .unwrap()
                .to_owned();
            metadata.packages[source].dependencies.push(Dependency {
                name: "renamed-capability".into(),
                path: Some(path),
                kind: kind.map(str::to_owned),
            });
            assert!(validate(&metadata).is_err());
        }
    }
}

#[test]
fn shared_dependencies_are_allowed_without_opening_same_layer_local_access() {
    for (source, target) in [(3, 6), (7, 6), (4, 8), (9, 8)] {
        let (_root, mut metadata) = fixture();
        metadata.resolve.nodes[source]
            .deps
            .push(ResolvedDependency {
                pkg: metadata.packages[target].id.clone(),
            });
        assert!(validate(&metadata).is_ok());
    }
    let (_root, mut metadata) = fixture();
    metadata.resolve.nodes[9].deps.push(ResolvedDependency {
        pkg: metadata.packages[6].id.clone(),
    });
    metadata.resolve.nodes[6].deps.push(ResolvedDependency {
        pkg: metadata.packages[3].id.clone(),
    });
    assert!(validate(&metadata).is_err());
}

#[test]
fn server_and_shared_packages_cannot_add_document_or_browser_adapters() {
    for source in [6, 7, 8, 9] {
        for name in [
            "csv",
            "tempfile",
            "html-escape",
            "rustix",
            "tokio-tungstenite",
        ] {
            let (_root, mut metadata) = fixture();
            metadata.packages[source].dependencies.push(Dependency {
                name: name.into(),
                path: None,
                kind: None,
            });
            assert!(validate(&metadata).is_err());
        }
    }
}
