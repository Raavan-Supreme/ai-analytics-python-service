package com.ai.analytics.backend.repository;

import com.ai.analytics.backend.model.AnalysisSession;
import com.ai.analytics.backend.model.User;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface AnalysisSessionRepository extends JpaRepository<AnalysisSession, Long> {
    List<AnalysisSession> findByOwner(User owner);
}
