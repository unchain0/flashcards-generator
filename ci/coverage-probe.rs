fn branch_if(value: bool) -> u8 {
    if value { 1 } else { 0 }
}

fn branch_and(left: bool, right: bool) -> u8 {
    if left && right { 1 } else { 0 }
}

fn branch_or(left: bool, right: bool) -> u8 {
    if left || right { 1 } else { 0 }
}

fn branch_match(value: Result<u8, u8>) -> u8 {
    match value {
        Ok(value) => value,
        Err(value) => value + 1,
    }
}

fn branch_question(value: Result<u8, u8>) -> Result<u8, u8> {
    let value = value?;
    Ok(value + 1)
}

fn branch_let_else(value: Option<u8>) -> u8 {
    let Some(value) = value else { return 0 };
    value
}

fn branch_if_let(value: Option<u8>) -> u8 {
    if let Some(value) = value { value } else { 0 }
}

fn branch_for(values: &[u8]) -> u8 {
    let mut sum = 0;
    for value in values {
        sum += value;
    }
    sum
}

fn branch_while(mut value: u8) -> u8 {
    let mut count = 0;
    while value > 0 {
        value -= 1;
        count += 1;
    }
    count
}

fn branch_while_let(values: &[u8]) -> u8 {
    let mut values = values.iter();
    let mut sum = 0;
    while let Some(value) = values.next() {
        sum += value;
    }
    sum
}

macro_rules! choose_branch {
    ($value:expr) => {
        if $value { 1 } else { 0 }
    };
}

fn branch_macro(value: bool) -> u8 {
    choose_branch!(value)
}

struct OnePending {
    value: bool,
    pending: bool,
}

impl std::future::Future for OnePending {
    type Output = bool;

    fn poll(
        self: std::pin::Pin<&mut Self>,
        _context: &mut std::task::Context<'_>,
    ) -> std::task::Poll<bool> {
        let state = self.get_mut();
        if state.pending {
            state.pending = false;
            std::task::Poll::Pending
        } else {
            std::task::Poll::Ready(state.value)
        }
    }
}

async fn branch_async(value: bool) -> u8 {
    if (OnePending {
        value,
        pending: true,
    })
    .await
    {
        1
    } else {
        0
    }
}

#[test]
fn exercises_if_short_circuit_and_macro_outcomes() {
    for value in [false, true] {
        assert_eq!(branch_if(value), u8::from(value));
        assert_eq!(branch_macro(value), u8::from(value));
    }
    for (left, right) in [(false, false), (false, true), (true, false), (true, true)] {
        assert_eq!(branch_and(left, right), u8::from(left && right));
        assert_eq!(branch_or(left, right), u8::from(left || right));
    }
}

#[test]
fn exercises_match_try_and_let_else_outcomes() {
    assert_eq!(branch_match(Ok(2)), 2);
    assert_eq!(branch_match(Err(2)), 3);
    assert_eq!(branch_question(Ok(2)), Ok(3));
    assert_eq!(branch_question(Err(2)), Err(2));
    assert_eq!(branch_let_else(Some(2)), 2);
    assert_eq!(branch_let_else(None), 0);
}

#[test]
fn exercises_pattern_and_loop_outcomes() {
    assert_eq!(branch_if_let(Some(2)), 2);
    assert_eq!(branch_if_let(None), 0);
    for values in [&[][..], &[1, 2][..]] {
        assert_eq!(branch_for(values), values.iter().sum());
        assert_eq!(branch_while_let(values), values.iter().sum());
    }
    assert_eq!(branch_while(0), 0);
    assert_eq!(branch_while(2), 2);
}

#[test]
fn exercises_async_outcomes() {
    use std::{
        future::Future,
        task::{Context, Poll, Waker},
    };
    let mut context = Context::from_waker(Waker::noop());
    for value in [false, true] {
        let mut future = std::pin::pin!(branch_async(value));
        assert_eq!(future.as_mut().poll(&mut context), Poll::Pending);
        assert_eq!(
            future.as_mut().poll(&mut context),
            Poll::Ready(u8::from(value))
        );
    }
}
