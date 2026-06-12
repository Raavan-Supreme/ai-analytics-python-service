package com.ai.analytics.backend.repository;

import com.ai.analytics.backend.model.Dashboard;
import com.ai.analytics.backend.model.User;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface DashboardRepository extends JpaRepository<Dashboard, Long> {
    List<Dashboard> findByOwnerOrderByUpdatedAtDesc(User owner);
}
