use super::*;

struct DefaultRetryFactory<'a>(&'a NotebookLMClient);

impl TemporaryNotebookFactory for DefaultRetryFactory<'_> {
    type Gateway = NotebookLMClient;
    type Scope<'a>
        = TemporaryNotebookScope<'a>
    where
        Self: 'a;

    async fn create_temporary_notebook(
        &self,
        title: &str,
    ) -> Result<Self::Scope<'_>, NotebookLMError> {
        self.0.create_temporary_notebook(title).await
    }
}

pub(super) async fn check_retry_policy(scenario: &Scenario) {
    let factory = DefaultRetryFactory(&scenario.client);
    let delay = factory.wait(Duration::from_secs(3600));
    tokio::pin!(delay);
    let mut context = std::task::Context::from_waker(std::task::Waker::noop());
    assert!(std::future::Future::poll(delay.as_mut(), &mut context).is_ready());

    scenario.state.mode.store(8, Ordering::SeqCst);
    scenario.state.attempts.store(0, Ordering::SeqCst);
    let before = scenario.state.sequence.load(Ordering::SeqCst);
    let result = generate_prepared_document(
        &LocalDocumentPreparer::default(),
        &factory,
        &scenario.temporary.path().join("Chapters.pdf"),
        "Study Root",
        &scenario.options,
    )
    .await;
    assert!(matches!(result, Err(PreparedGenerationError::Generation {
        completed, failed_chunk: 1,
        error: GenerationError::Provider(NotebookLMError::Status(StatusCode::TOO_MANY_REQUESTS)),
    }) if completed.is_empty()));
    assert_eq!(scenario.state.attempts.load(Ordering::SeqCst), 1);
    assert_eq!(scenario.state.sequence.load(Ordering::SeqCst), before + 1);
    assert!(scenario.client.finish_cleanup().await);
    assert!(
        scenario
            .state
            .deleted
            .lock()
            .unwrap()
            .contains(&format!("owned-{before}"))
    );
    check_bounded_retry_policy(scenario).await;
}

struct RecordingRetryFactory<'a> {
    client: &'a NotebookLMClient,
    delays: Mutex<Vec<Duration>>,
}

impl TemporaryNotebookFactory for RecordingRetryFactory<'_> {
    type Gateway = NotebookLMClient;
    type Scope<'a>
        = TemporaryNotebookScope<'a>
    where
        Self: 'a;

    fn is_transient_error(&self, error: &NotebookLMError) -> bool {
        self.client.is_transient_error(error)
    }

    async fn wait(&self, delay: Duration) {
        self.delays.lock().unwrap().push(delay);
    }

    async fn create_temporary_notebook(
        &self,
        title: &str,
    ) -> Result<Self::Scope<'_>, NotebookLMError> {
        self.client.create_temporary_notebook(title).await
    }
}

async fn check_bounded_retry_policy(scenario: &Scenario) {
    for selected in [0, 7, 8, 9, 10] {
        let factory = RecordingRetryFactory {
            client: &scenario.client,
            delays: Mutex::new(Vec::new()),
        };
        scenario.state.mode.store(selected, Ordering::SeqCst);
        scenario.state.attempts.store(0, Ordering::SeqCst);
        let before = scenario.state.sequence.load(Ordering::SeqCst);
        let result = generate_prepared_document(
            &LocalDocumentPreparer::default(),
            &factory,
            &scenario.temporary.path().join("Chapters.pdf"),
            "Study Root",
            &scenario.options,
        )
        .await;
        let (attempts, created, successful, expected_delays) = match selected {
            0 => (3, 3, true, vec![5, 5]),
            7 => (5, 5, true, vec![5, 10, 5, 5]),
            8 => (3, 3, false, vec![5, 10]),
            9 => (1, 0, false, vec![]),
            10 => (1, 1, false, vec![]),
            _ => panic!("Unexpected retry fixture"),
        };
        assert_eq!(result.is_ok(), successful);
        assert_eq!(scenario.state.attempts.load(Ordering::SeqCst), attempts);
        assert_eq!(
            scenario.state.sequence.load(Ordering::SeqCst),
            before + created
        );
        assert_eq!(
            *factory.delays.lock().unwrap(),
            expected_delays
                .into_iter()
                .map(Duration::from_secs)
                .collect::<Vec<_>>()
        );
        assert_eq!(scenario.client.finish_cleanup().await, selected != 10);
        for index in before..before + created {
            assert!(
                scenario
                    .state
                    .deleted
                    .lock()
                    .unwrap()
                    .contains(&format!("owned-{index}"))
            );
        }
    }
}
