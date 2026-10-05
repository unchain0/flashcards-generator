use crate::{CheckResult, metadata::read_bounded};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};
use syn::visit::{self, Visit};

#[derive(Default)]
pub(super) struct Source {
    pub executable: bool,
    pub constructions: BTreeSet<&'static str>,
    hidden_coverage: bool,
}

pub(super) fn inventory(roots: &[PathBuf]) -> CheckResult<BTreeMap<PathBuf, Source>> {
    let mut sources = BTreeMap::new();
    for root in roots {
        collect(root, &mut sources)?;
    }
    Ok(sources)
}

fn collect(directory: &Path, sources: &mut BTreeMap<PathBuf, Source>) -> CheckResult<()> {
    for entry in fs::read_dir(directory)? {
        collect_entry(&entry?, sources)?;
    }
    Ok(())
}

fn collect_entry(entry: &fs::DirEntry, sources: &mut BTreeMap<PathBuf, Source>) -> CheckResult<()> {
    let path = entry.path();
    let name = entry.file_name();
    let name = name.to_str().ok_or("Non-UTF-8 production source name")?;
    let kind = entry.file_type()?;
    if kind.is_symlink() {
        return Err("Production source inventory does not follow symlinks".into());
    }
    if (name == "tests" || name == "tests.rs" || name.ends_with("_tests.rs"))
        && declared_test_module(&path)?
    {
        return Ok(());
    }
    if kind.is_dir() {
        return collect(&path, sources);
    }
    if path.extension().is_none_or(|extension| extension != "rs") {
        return Ok(());
    }
    sources.insert(path.canonicalize()?, parse(&path)?);
    Ok(())
}

fn declared_test_module(path: &Path) -> CheckResult<bool> {
    let parent = path.parent().ok_or("Test module has no parent")?;
    let module = path
        .file_stem()
        .and_then(|name| name.to_str())
        .ok_or("Non-UTF-8 test module name")?;
    for owner in [
        parent.with_extension("rs"),
        parent.join("mod.rs"),
        parent.join("lib.rs"),
        parent.join("main.rs"),
    ] {
        if !owner.is_file() {
            continue;
        }
        if owner.is_symlink() {
            return Err("Production source inventory does not follow symlinks".into());
        }
        let raw = read_bounded(&owner, 4 * 1024 * 1024)?;
        let file = syn::parse_file(std::str::from_utf8(&raw)?)?;
        if file.items.iter().any(|item| {
            matches!(item, syn::Item::Mod(item)
                if item.ident == module && item.content.is_none() && test_only(&item.attrs)
                    && !item.attrs.iter().any(|attribute| attribute.path().is_ident("path")))
        }) {
            return Ok(true);
        }
    }
    Ok(false)
}

fn parse(path: &Path) -> CheckResult<Source> {
    let raw = read_bounded(path, 4 * 1024 * 1024)?;
    let file = syn::parse_file(std::str::from_utf8(&raw)?)?;
    let mut source = Source::default();
    source.visit_file(&file);
    if source.hidden_coverage {
        return Err(format!("Coverage-altering attribute in {}", path.display()).into());
    }
    Ok(source)
}

fn test_only(attributes: &[syn::Attribute]) -> bool {
    attributes.iter().any(|attribute| {
        attribute.path().is_ident("test")
            || (attribute.path().is_ident("cfg")
                && attribute
                    .parse_args::<syn::Meta>()
                    .is_ok_and(|meta| requires_test(&meta)))
    })
}

fn requires_test(meta: &syn::Meta) -> bool {
    match meta {
        syn::Meta::Path(path) => path.is_ident("test"),
        syn::Meta::List(list) if list.path.is_ident("all") || list.path.is_ident("any") => {
            let Ok(predicates) = list.parse_args_with(
                syn::punctuated::Punctuated::<syn::Meta, syn::Token![,]>::parse_terminated,
            ) else {
                return false;
            };
            if list.path.is_ident("all") {
                predicates.iter().any(requires_test)
            } else {
                !predicates.is_empty() && predicates.iter().all(requires_test)
            }
        }
        _ => false,
    }
}

impl<'ast> Visit<'ast> for Source {
    fn visit_item_fn(&mut self, item: &'ast syn::ItemFn) {
        if test_only(&item.attrs) {
            return;
        }
        self.executable = true;
        visit::visit_item_fn(self, item);
    }

    fn visit_impl_item_fn(&mut self, item: &'ast syn::ImplItemFn) {
        if test_only(&item.attrs) {
            return;
        }
        self.executable = true;
        visit::visit_impl_item_fn(self, item);
    }

    fn visit_item_mod(&mut self, item: &'ast syn::ItemMod) {
        if test_only(&item.attrs) {
            return;
        }
        visit::visit_item_mod(self, item);
    }

    fn visit_trait_item_fn(&mut self, item: &'ast syn::TraitItemFn) {
        self.executable |= item.default.is_some();
        visit::visit_trait_item_fn(self, item);
    }

    fn visit_expr_if(&mut self, expression: &'ast syn::ExprIf) {
        self.constructions.insert("branch_if");
        visit::visit_expr_if(self, expression);
    }

    fn visit_expr_let(&mut self, expression: &'ast syn::ExprLet) {
        self.constructions.insert("branch_if_let");
        visit::visit_expr_let(self, expression);
    }

    fn visit_expr_for_loop(&mut self, expression: &'ast syn::ExprForLoop) {
        self.constructions.insert("branch_for");
        visit::visit_expr_for_loop(self, expression);
    }

    fn visit_expr_while(&mut self, expression: &'ast syn::ExprWhile) {
        let probe = if matches!(*expression.cond, syn::Expr::Let(_)) {
            "branch_while_let"
        } else {
            "branch_while"
        };
        self.constructions.insert(probe);
        visit::visit_expr_while(self, expression);
    }

    fn visit_expr_match(&mut self, expression: &'ast syn::ExprMatch) {
        self.constructions.insert("branch_match");
        visit::visit_expr_match(self, expression);
    }

    fn visit_expr_try(&mut self, expression: &'ast syn::ExprTry) {
        self.constructions.insert("branch_question");
        visit::visit_expr_try(self, expression);
    }

    fn visit_expr_await(&mut self, expression: &'ast syn::ExprAwait) {
        self.constructions.insert("branch_async");
        visit::visit_expr_await(self, expression);
    }

    fn visit_expr_binary(&mut self, expression: &'ast syn::ExprBinary) {
        match expression.op {
            syn::BinOp::And(_) => {
                self.constructions.insert("branch_and");
            }
            syn::BinOp::Or(_) => {
                self.constructions.insert("branch_or");
            }
            _ => {}
        }
        visit::visit_expr_binary(self, expression);
    }

    fn visit_local_init(&mut self, local: &'ast syn::LocalInit) {
        if local.diverge.is_some() {
            self.constructions.insert("branch_let_else");
        }
        visit::visit_local_init(self, local);
    }

    fn visit_attribute(&mut self, attribute: &'ast syn::Attribute) {
        self.executable |= attribute.path().is_ident("derive");
        self.hidden_coverage |= alters_coverage(attribute);
        visit::visit_attribute(self, attribute);
    }
}

fn alters_coverage(attribute: &syn::Attribute) -> bool {
    if attribute.path().is_ident("coverage") {
        return true;
    }
    let syn::Meta::List(list) = &attribute.meta else {
        return false;
    };
    if !list.path.is_ident("cfg") && !list.path.is_ident("cfg_attr") {
        return false;
    }
    list.tokens
        .to_string()
        .split(|character: char| !character.is_alphanumeric() && character != '_')
        .any(|word| matches!(word, "coverage" | "coverage_nightly"))
}

#[cfg(test)]
mod tests;
